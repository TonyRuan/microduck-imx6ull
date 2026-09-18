#!/usr/bin/env python3
"""Focused-window keyboard teleoperation over robotd's existing JSON-RPC socket."""

import argparse
from collections import deque
import json
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time


SKILLS = {"ctrl": "sit_toggle", "space": "roulade", "j": "kick_left", "k": "kick_right", "p": "ground_pick"}
KEYS = {"w", "a", "s", "d", *SKILLS}
TICK = 0.05
LEASE = 0.25
# Command limits, not guaranteed physical speeds. See simulation.md for the
# alpha_walking measurements behind the default and the low-speed warning.
SPEED_LIMITS = (0.40, 0.40, 1.0)
SPEED_PRESETS = {"仿真实测": (0.30, 0.40, 0.40), "低速试探": (0.10, 0.08, 0.40)}


def speed_hint(forward, backward, policy='alpha'):
    if policy == 'roller':
        return '轮式实验：P 为蹲伏；不支持足式踢球／翻滚。稳定性尚未通过验收。'
    if policy == 'velstand':
        return 'velstand 低速起步可能原地踏步；前进 0.40 m/s 已验证可起步。指令速度不等于实际速度。'
    if 0 < forward < 0.30 or 0 < backward < 0.40:
        return "低速可能不迈步：alpha 步态在后退 0.08–0.30 m/s 时几乎原地。"
    return "alpha 仿真实测：前进 0.30、后退 0.40 m/s 可迈步；后退可能侧偏。"


class RpcError(Exception):
    pass


class RobotClient:
    def __init__(self, path):
        self.path = str(path)

    def call(self, method, params=None):
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.15)
            sock.connect(self.path)
            sock.sendall((json.dumps(request) + "\n").encode())
            with sock.makefile("rb") as stream:
                line = stream.readline(65537)
        if not line.endswith(b"\n") or len(line) > 65536:
            raise OSError("Incomplete robotd response")
        response = json.loads(line)
        if response.get("id") != 1:
            raise OSError("Unexpected robotd response")
        if "error" in response:
            raise RpcError(response["error"].get("message", "RPC error"))
        result = response.get("result", {})
        if isinstance(result, dict) and result.get("accepted") is False:
            raise RpcError(result.get("reason", "Action refused"))
        return result

    def move(self, vx=0.0, vyaw=0.0):
        return self.call("robot.move", {"vx": vx, "vy": 0.0, "vyaw": vyaw})


class Controls:
    """Physical presses: repeated keydown events never retrigger a skill."""

    def __init__(self):
        self.pressed = set()
        self.blocked = set()

    def press(self, key):
        if key in self.pressed:
            return None
        self.pressed.add(key)
        skill = SKILLS.get(key)
        if skill:
            self.block_movement()
        return skill

    def block_movement(self):
        self.blocked.update(self.pressed & {"w", "a", "s", "d"})

    def release(self, key):
        self.pressed.discard(key)
        self.blocked.discard(key)

    def clear(self):
        self.pressed.clear()
        self.blocked.clear()

    def twist(self, forward, backward, turn):
        active = self.pressed - self.blocked
        longitudinal = int("w" in active) - int("s" in active)
        vx = longitudinal * (forward if longitudinal > 0 else backward)
        return vx, (int("a" in active) - int("d" in active)) * turn


class Link(threading.Thread):
    """Bounded I/O off the UI thread, with a lease so a stalled UI stops motion."""

    def __init__(self, client):
        super().__init__(daemon=True)
        self.client = client
        self.events = queue.SimpleQueue()
        self.lock = threading.Lock()
        self.intent = (0.0, 0.0, 0.0)
        self.actions = deque(maxlen=1)
        self.connect_requested = threading.Event()
        self.done = threading.Event()

    def update(self, vx, vyaw):
        with self.lock:
            self.intent = (vx, vyaw, time.monotonic() + LEASE)

    def stop_motion(self):
        with self.lock:
            self.intent = (0.0, 0.0, 0.0)
            self.actions.clear()

    def skill(self, name):
        with self.lock:
            self.intent = (0.0, 0.0, 0.0)
            self.actions.append((name, time.monotonic() + LEASE))

    def next_command(self, now):
        with self.lock:
            vx, vyaw, expires = self.intent
            action = self.actions.popleft() if self.actions else None
        if expires <= now:
            vx = vyaw = 0.0
        skill = action[0] if action and action[1] > now else None
        return vx, vyaw, skill

    def run(self):
        connected = False
        next_health = 0.0
        try:
            while not self.done.is_set():
                started = time.monotonic()
                try:
                    if self.connect_requested.is_set():
                        self.connect_requested.clear()
                        self.stop_motion()
                        connected = False
                        if hasattr(self.client, 'connect'):
                            self.client.connect()
                        self.client.call("robot.health")
                        connected = True
                        next_health = 0.0
                        self.events.put(("connected", "已连接 · 点击本窗口后使用键盘"))
                    if connected:
                        if hasattr(self.client, 'connect') and time.monotonic() >= next_health:
                            health = self.client.call('robot.health')
                            if not health.get('healthy'):
                                raise OSError('控制器健康检查失败')
                            loop = health.get('control_loop', {})
                            hz = loop.get('achieved_hz')
                            self.events.put(('health', f'{hz:.1f} Hz · 超时 {loop.get("missed", 0)}' if hz is not None else '控制循环启动中'))
                            next_health = time.monotonic()+1
                        vx, vyaw, skill = self.next_command(time.monotonic())
                        self.client.move(0.0 if skill else vx, 0.0 if skill else vyaw)
                        if skill:
                            self.client.call("robot.do", {"skill": skill})
                            self.events.put(("action", f"已接受动作：{skill}"))
                except RpcError as error:
                    self.stop_motion()
                    self.events.put(("refused", str(error)))
                    if not connected:
                        self.events.put(('disconnected', str(error)))
                        if hasattr(self.client, 'disconnect'):
                            self.client.disconnect()
                except (OSError, ValueError, TypeError) as error:
                    connected = False
                    self.stop_motion()
                    self.events.put(("disconnected", f"未连接：{error} · 排除问题后点重新连接"))
                    if hasattr(self.client, 'disconnect'):
                        try:
                            self.client.disconnect()
                        except OSError as cleanup:
                            self.events.put(('disconnected', str(cleanup)))
                self.done.wait(max(0.0, TICK - (time.monotonic() - started)))
        finally:
            if connected:
                try:
                    self.client.move()
                except (OSError, ValueError, RpcError):
                    pass
            if hasattr(self.client, 'disconnect'):
                self.client.disconnect()


def run_gui(path=None, rl=None, viewer=True):
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as error:
        raise SystemExit("需要 Tk GUI 支持。macOS：brew install python-tk@3.12；然后使用 python3.12 启动。") from error

    root = tk.Tk()
    root.title("Microduck · 键盘遥控")
    root.geometry("980x850")
    root.minsize(940, 850)
    root.configure(bg="#eff4f2")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", background="#eff4f2", foreground="#183e36", padding=7)
    style.map("TButton", background=[("active", "#c4edb9")])
    style.configure("Horizontal.TScale", background="#eff4f2", troughcolor="#d4dfda")
    controls = Controls()
    from duck_backends import ManagedClient, board_ports, PROFILES
    managed = ManagedClient(rl or Path(__file__).resolve().parents[2]/'microduck_rl', viewer) if path is None else None
    link = Link(managed or RobotClient(path))
    connected = False
    pending_release = {}
    labels = {}
    status = tk.StringVar(value="正在连接本地仿真…")
    focus_text = tk.StringVar(value="点击此窗口启用键盘")
    velocity = tk.StringVar(value="0.00 m/s   ·   0.00 rad/s")
    action_text = tk.StringVar(value="动作按一次触发一次；执行动作前会清除行走输入。")
    presets_for_policy = {'步态起步': (0.40, 0.40, 0.40), '低速试探': SPEED_PRESETS['低速试探']} if managed else SPEED_PRESETS
    default_speeds = next(iter(presets_for_policy.values()))
    forward, backward, turn = [tk.DoubleVar(value=value) for value in default_speeds]
    policy_choice = tk.StringVar(value=PROFILES['velstand'][0])
    def selected_policy():
        return next(name for name, profile in PROFILES.items() if profile[0] == policy_choice.get())
    def current_speed_hint():
        return speed_hint(forward.get(), backward.get(), managed.policy_profile if managed else 'alpha')
    speed_text = tk.StringVar(value=current_speed_hint())
    backend = tk.StringVar(value='Mac 本地')
    usb_port = tk.StringVar(value=next(iter(board_ports()), ''))
    board_password = tk.StringVar()
    remember_password = tk.BooleanVar(value=sys.platform == 'darwin')
    device_text = tk.StringVar(value='正在检测 USB…')
    backend_text = tk.StringVar(value='尚未启动控制后端')
    health_text = tk.StringVar(value='控制循环：未连接')
    busy = False
    closing = False
    skill_buttons = {}
    compact = False
    expanded_geometry = None
    expanded_topmost = False
    mini_state = tk.StringVar(value='正在连接…')
    mini_hint = tk.StringVar(value='点击此小窗，再使用 WASD')

    def text(parent, value=None, variable=None, size=13, color="#183e36", **kwargs):
        return tk.Label(parent, text=value, textvariable=variable, bg=parent.cget("bg"),
                        fg=color, font=("Helvetica", size), **kwargs)

    outer = tk.Frame(root, bg='#eff4f2')
    outer.pack(fill='both', expand=True)
    sidebar = tk.Frame(outer, bg='#e2ebe6', padx=18, pady=22, width=305)
    sidebar.pack(side='left', fill='y')
    sidebar.pack_propagate(False)
    text(sidebar, 'CONTROL BACKEND', size=11, anchor='w').pack(fill='x')
    text(sidebar, '运控后端', size=22, anchor='w').pack(fill='x', pady=(8,16))
    selector = ttk.Combobox(sidebar, textvariable=backend, values=['Mac 本地', '嵌入式 i.MX6ULL'], state='readonly')
    selector.pack(fill='x')
    text(sidebar, '策略组合（切换后生效）', size=11, anchor='w').pack(fill='x', pady=(10,4))
    policy_selector = ttk.Combobox(sidebar, textvariable=policy_choice,
                                   values=[profile[0] for profile in PROFILES.values()], state='readonly')
    policy_selector.pack(fill='x')
    text(sidebar, '切换会先停步，并重置本面板的仿真。\n不会接管其他窗口的模拟器。', size=11, justify='left', wraplength=260).pack(fill='x', pady=12)
    text(sidebar, '开发板 USB 串口', size=12, anchor='w').pack(fill='x', pady=(10,5))
    ports_widget = ttk.Combobox(sidebar, textvariable=usb_port, values=board_ports(), state='readonly')
    ports_widget.pack(fill='x')
    text(sidebar, variable=device_text, size=11, anchor='w', wraplength=265, justify='left').pack(fill='x', pady=8)
    text(sidebar, '开发板密码（已记住时可留空）', size=11, anchor='w').pack(fill='x', pady=(12,5))
    password_entry = ttk.Entry(sidebar, textvariable=board_password, show='•')
    password_entry.pack(fill='x')
    credential_row = tk.Frame(sidebar, bg='#e2ebe6')
    credential_row.pack(fill='x', pady=(5,0))
    remember_widget = ttk.Checkbutton(credential_row, text='记住密码（钥匙串）', variable=remember_password)
    remember_widget.pack(side='left')
    def forget_password():
        nonlocal busy
        if not managed or busy or closing: return
        clear()
        board_password.set('')
        remember_password.set(False)
        port = usb_port.get()
        busy = True
        switch_button.configure(state='disabled')
        forget_button.configure(state='disabled')
        def remove():
            try:
                managed.credentials.forget(port)
                message = '已删除该串口的钥匙串密码；当前连接不受影响'
            except OSError as error:
                message = str(error)
            managed.events.put(('forgotten', message))
        threading.Thread(target=remove, daemon=True).start()
    forget_button = ttk.Button(credential_row, text='忘记', command=forget_password, takefocus=False)
    forget_button.pack(side='right')
    if not managed or sys.platform != 'darwin':
        remember_widget.configure(state='disabled')
        forget_button.configure(state='disabled')
    switch_button = ttk.Button(sidebar, text='启动 / 切换后端', command=lambda: reconnect())
    switch_button.pack(fill='x', pady=8)
    text(sidebar, variable=backend_text, size=12, anchor='w', wraplength=265, justify='left').pack(fill='x', pady=12)
    text(sidebar, variable=health_text, size=13, anchor='w', wraplength=265).pack(fill='x', pady=10)
    text(sidebar, '硬件在环：传感器与电机输出由 Mac 模拟器提供，不驱动真实电机。\n两端支持全部模型；轮式组合会切换带轮场景。', size=11, anchor='w', wraplength=265, justify='left').pack(fill='x', pady=6)
    if not managed:
        selector.configure(state='disabled')
        policy_selector.configure(state='disabled')
        switch_button.configure(state='disabled')
        backend_text.set('外部 socket 模式：不管理后端')
    main = tk.Frame(outer, bg="#eff4f2", padx=22, pady=14)
    main.pack(side='right', fill="both", expand=True)
    heading = tk.Frame(main, bg='#eff4f2')
    heading.pack(fill='x')
    text(heading, "MICRODUCK  /  TELEOP", size=11, color="#537a6c", anchor="w").pack(side='left')
    ttk.Button(heading, text='缩小 · 迷你模式', takefocus=False,
               command=lambda: toggle_compact()).pack(side='right')
    text(main, "键盘遥控", size=28, anchor="w").pack(fill="x", pady=(5, 8))
    text(main, variable=status, size=12, anchor="w", wraplength=550, justify="left").pack(fill="x")
    text(main, variable=focus_text, size=12, color="#648078", anchor="w").pack(fill="x", pady=(4, 8))

    pad = tk.Frame(main, bg="white", padx=20, pady=8)
    pad.pack(fill="x")
    pad.columnconfigure((0, 1, 2), weight=1)
    for key, caption, row, col in [("w", "W  前进", 0, 1), ("a", "A  左转", 1, 0),
                                    ("s", "S  后退", 1, 1), ("d", "D  右转", 1, 2)]:
        label = tk.Label(pad, text=caption, bg="#eff4f2", fg="#183e36", pady=8,
                         font=("Helvetica", 15, "bold"))
        label.grid(row=row, column=col, padx=4, pady=4, sticky="ew")
        labels[key] = label
    text(pad, variable=velocity, size=14).grid(row=2, column=0, columnspan=3, pady=(10, 0))

    sliders = tk.Frame(main, bg="#eff4f2")
    sliders.pack(fill="x", pady=(10, 10))
    for title, variable, upper, unit in zip(
            ("前进速度", "后退速度", "转向速度"), (forward, backward, turn),
            SPEED_LIMITS, ("m/s", "m/s", "rad/s")):
        row = tk.Frame(sliders, bg="#eff4f2")
        row.pack(fill="x", pady=3)
        text(row, title, size=13).pack(side="left")
        value_label = text(row, f"{variable.get():.2f} {unit}", size=12, width=12, anchor="e")
        value_label.pack(side="right")
        def changed(*_, variable=variable, label=value_label, suffix=unit):
            label.config(text=f"{variable.get():.2f} {suffix}")
            speed_text.set(current_speed_hint())
        # A trace also refreshes labels when a preset changes the variables.
        variable.trace_add("write", changed)
        slider = ttk.Scale(row, from_=0.0, to=upper, variable=variable)
        slider.pack(side="left", fill="x", expand=True, padx=15)
        def step(event, variable=variable, upper=upper):
            value = min(upper, max(0.0, variable.get() + (0.01 if event.keysym == "Right" else -0.01)))
            variable.set(value)
            return "break"
        slider.bind("<Left>", step)
        slider.bind("<Right>", step)

    presets = tk.Frame(main, bg="#eff4f2")
    presets.pack(fill="x")

    def apply_preset(name):
        # Do not accelerate a key already held when changing a whole preset.
        controls.block_movement()
        link.stop_motion()
        for variable, value in zip((forward, backward, turn), presets_for_policy[name]):
            variable.set(value)
        action_text.set(f"已选 {name} · 重新按方向键移动")

    for name in presets_for_policy:
        button = ttk.Button(presets, text=name, command=lambda name=name: apply_preset(name))
        button.pack(side="left", padx=(0, 8))
        # Space belongs to the roll action everywhere in the panel; Enter keeps
        # the preset buttons accessible without changing that control contract.
        button.bind("<Return>", lambda event, name=name: apply_preset(name))
    text(main, variable=speed_text, size=11, color="#906020", wraplength=560,
         justify="left", anchor="w").pack(fill="x", pady=(6, 2))
    text(main, "以上为指令速度，并非实测速度或硬件极限。", size=11,
         color="#648078", anchor="w").pack(fill="x", pady=(0, 8))

    actions = tk.Frame(main, bg="white", padx=15, pady=8)
    actions.pack(fill="x")
    actions.columnconfigure((0, 1), weight=1)

    def clear():
        controls.clear()
        for callback in pending_release.values():
            root.after_cancel(callback)
        pending_release.clear()
        link.stop_motion()

    def perform(skill):
        controls.block_movement()
        if connected:
            if managed and skill not in managed.skills:
                action_text.set('当前后端未移植 / 未加载此动作模型')
                return
            link.skill(skill)
            action_text.set(f"正在请求：{skill}")

    for index, (key, title) in enumerate([("ctrl", "Ctrl  坐下 / 站起"), ("space", "空格  向前翻滚"),
                                         ("j", "J  左脚踢球"), ("k", "K  右脚踢球")]):
        button = ttk.Button(actions, text=title, takefocus=False, command=lambda key=key: perform(SKILLS[key]))
        button.grid(row=index // 2, column=index % 2, sticky="ew", padx=5, pady=5)
        skill_buttons[SKILLS[key]] = button
    text(main, variable=action_text, size=11, wraplength=550, justify="left", anchor="w").pack(fill="x", pady=10)
    footer = tk.Frame(main, bg="#eff4f2")
    footer.pack(fill="x")

    def stop():
        controls.block_movement()
        link.stop_motion()
        action_text.set("已停止行走 · 已接受的翻滚 / 踢球由策略完成")

    def reconnect():
        nonlocal connected, busy
        if busy or closing:
            return
        clear()
        if managed:
            kind = 'mac' if backend.get() == 'Mac 本地' else 'board'
            password = board_password.get() or os.environ.get('MICRODUCK_BOARD_PASSWORD', '')
            if kind == 'board' and not password and not remember_password.get():
                status.set('请输入开发板密码再连接')
                return
            managed.select(kind, usb_port.get(), password if kind == 'board' else '',
                           remember=remember_password.get(), policy_profile=selected_policy())
            speed_text.set(current_speed_hint())
            if kind == 'board': board_password.set('')
            busy = True
            switch_button.configure(state='disabled')
            forget_button.configure(state='disabled')
        connected = False
        health_text.set('控制循环：连接中')
        status.set("正在重新连接…")
        link.connect_requested.set()

    ttk.Button(footer, text="停止行走  Esc", command=stop, takefocus=False).pack(side="left")
    pick_button = ttk.Button(footer, text='P  捡拾 / 轮式蹲伏', command=lambda: perform('ground_pick'), takefocus=False)
    pick_button.pack(side='left', padx=8)
    skill_buttons['ground_pick'] = pick_button
    ttk.Button(footer, text="重新连接", command=reconnect, takefocus=False).pack(side="right")
    text(main, "松开方向键即停止；切换窗口自动停止。\n只在本窗口接收按键，可组合 W+A / W+D 转弯。", size=11,
         color="#648078", justify="left", anchor="w").pack(fill="x", pady=(8, 4))
    text(main, str(path) if path else '独立仿真实例 · 本面板管理生命周期', size=10, color="#648078", wraplength=550, anchor="w").pack(fill="x")

    mini = tk.Frame(root, bg='#eff4f2', padx=12, pady=8)
    mini_header = tk.Frame(mini, bg='#eff4f2')
    mini_header.pack(fill='x')
    text(mini_header, 'MICRODUCK', size=11, color='#537a6c').pack(side='left')
    ttk.Button(mini_header, text='展开 ↗', takefocus=False,
               command=lambda: toggle_compact()).pack(side='right')
    text(mini, variable=mini_state, size=11, anchor='w').pack(fill='x', pady=(4,0))
    text(mini, variable=velocity, size=16, anchor='w').pack(fill='x', pady=5)
    text(mini, variable=mini_hint, size=10, anchor='w', wraplength=310,
         justify='left', color='#648078').pack(fill='x')
    ttk.Button(mini, text='停止行走 · Esc', command=stop,
               takefocus=False).pack(fill='x', pady=(7,0))

    def toggle_compact():
        nonlocal compact, expanded_geometry, expanded_topmost
        if closing: return
        # Only change presentation. Keep the same session, but require a fresh
        # keypress so a resize cannot carry held movement into the new layout.
        controls.block_movement()
        link.stop_motion()
        if not compact:
            expanded_geometry = root.geometry()
            expanded_topmost = root.attributes('-topmost')
            outer.pack_forget()
            root.minsize(340, 200)
            root.geometry(f'340x200+{root.winfo_x()}+{root.winfo_y()}')
            root.attributes('-topmost', True)
            mini.pack(fill='both', expand=True)
            root.title('Microduck · 迷你遥控')
        else:
            mini.pack_forget()
            root.minsize(940, 850)
            root.geometry(expanded_geometry)
            root.attributes('-topmost', expanded_topmost)
            outer.pack(fill='both', expand=True)
            root.title('Microduck · 键盘遥控')
        compact = not compact
        root.focus_set()

    def typing():
        widget = root.focus_get()
        return widget is not None and widget.winfo_class() in {'Entry', 'TEntry', 'TCombobox', 'TCheckbutton'}

    def normalized(event):
        return "ctrl" if event.keysym in {"Control_L", "Control_R"} else event.keysym.lower()

    def keydown(event):
        if typing():
            clear()
            return
        key = normalized(event)
        if key == "escape":
            stop()
            return "break"
        if key not in KEYS:
            return
        if key in pending_release:
            root.after_cancel(pending_release.pop(key))
        if connected:
            skill = controls.press(key)
            if skill:
                perform(skill)
        return "break"

    def keyup(event):
        key = normalized(event)
        if key not in KEYS:
            return
        def released():
            pending_release.pop(key, None)
            controls.release(key)
        if key in pending_release:
            root.after_cancel(pending_release[key])
        # X11 auto-repeat can be release+press; cancel the synthetic release on its press.
        pending_release[key] = root.after(20, released)
        return "break"

    def check_focus():
        if root.focus_displayof() is None:
            clear()

    def poll():
        nonlocal connected, busy
        if closing:
            return
        if managed:
            while not managed.events.empty():
                kind, message = managed.events.get()
                if kind in {'credential', 'forgotten'}:
                    action_text.set(message)
                    if kind == 'forgotten':
                        busy = False
                        switch_button.configure(state='normal')
                        forget_button.configure(state='normal')
                else:
                    backend_text.set(message)
        while not link.events.empty():
            kind, message = link.events.get()
            if kind in {"connected", "disconnected"}:
                clear()
                connected = kind == "connected"
                status.set(message)
                busy = False
                if managed:
                    switch_button.configure(state='normal')
                    if sys.platform == 'darwin': forget_button.configure(state='normal')
                if not connected: health_text.set('控制循环：未连接')
                refresh_devices()
            elif kind == 'health':
                health_text.set(message)
            else:
                if kind == "refused":
                    clear()
                action_text.set(message)
        focused = root.focus_displayof() is not None and not typing()
        if not focused:
            clear()
        vx, vyaw = controls.twist(forward.get(), backward.get(), turn.get()) if connected and focused else (0.0, 0.0)
        link.update(vx, vyaw)
        velocity.set(f"{vx:+.2f} m/s   ·   {vyaw:+.2f} rad/s")
        focus_text.set("键盘已就绪 · 按住方向键移动" if focused and connected else "键盘暂停 · 请连接并点击本窗口")
        backend_name = ('i.MX6ULL' if managed.active_kind == 'board' else 'Mac') if managed else '外部控制器'
        if connected:
            mini_state.set(f'● {backend_name} · {health_text.get()}')
            mini_hint.set('WASD 行走 / 转弯 · 松键停止' if focused else '键盘暂停 · 点击此小窗后使用 WASD')
        else:
            mini_state.set('○ 正在连接…' if busy else '○ 未连接 · 展开查看详情')
            mini_hint.set('可展开查看进度、重连或切换后端')
        for key, label in labels.items():
            label.configure(bg="#c4edb9" if key in controls.pressed - controls.blocked else "#eff4f2")
        for skill, button in skill_buttons.items():
            button.configure(state='normal' if connected and (not managed or skill in managed.skills) else 'disabled')
        root.after(50, poll)

    def close():
        nonlocal closing
        if closing: return
        closing = True
        clear()
        if modifier_monitor is not None:
            NSEvent.removeMonitor_(modifier_monitor)
        link.done.set()
        if managed: managed.cancel.set()
        status.set('正在停止后端并释放 USB，请稍候…')
        mini_state.set('正在停止后端…')
        mini_hint.set('正在释放连接，请稍候')
        def wait_closed():
            if link.is_alive(): root.after(100, wait_closed)
            else: root.destroy()
        wait_closed()

    # Consume control keys before widget defaults (Space otherwise activates a
    # focused button as well as requesting a roll).
    def install_bindtag(widget):
        widget.bindtags(("DuckControls", *widget.bindtags()))
        for child in widget.winfo_children():
            install_bindtag(child)
    install_bindtag(root)
    root.bind_class("DuckControls", "<KeyPress>", keydown)
    root.bind_class("DuckControls", "<KeyRelease>", keyup)
    root.bind("<FocusOut>", lambda event: root.after(20, check_focus))
    root.protocol("WM_DELETE_WINDOW", close)
    modifier_monitor = None
    if sys.platform == "darwin":
        try:
            from AppKit import NSEvent, NSEventMaskFlagsChanged, NSEventModifierFlagControl
        except ImportError as error:
            root.destroy()
            raise SystemExit("macOS Ctrl 支持需要：uv pip install --python <GUI Python> pyobjc-framework-Cocoa") from error

        ctrl_was_down = False
        modifier_events = deque()

        def record_modifiers(event):
            # Never call Tk from a Cocoa callback: only copy the event into a queue.
            modifier_events.append(bool(event.modifierFlags() & NSEventModifierFlagControl))
            return event

        modifier_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskFlagsChanged, record_modifiers)

        def modifiers():
            nonlocal ctrl_was_down
            # Consume events on Tk's own timer, preserving even short Ctrl taps.
            # The event monitor is app-local and needs no input-monitoring permission.
            while modifier_events:
                down = modifier_events.popleft()
                if down and not ctrl_was_down:
                    if connected and root.focus_displayof() is not None and not typing():
                        skill = controls.press("ctrl")
                        if skill:
                            perform(skill)
                elif not down:
                    controls.release("ctrl")
                ctrl_was_down = down
            root.after(10, modifiers)

        modifiers()
    link.start()
    def refresh_devices():
        ports = board_ports()
        ports_widget.configure(values=ports)
        if not usb_port.get() and ports: usb_port.set(ports[0])
        found = usb_port.get() in ports
        state = 'USB 已发现' if found else 'USB 未发现 / 已拔出'
        if managed and managed.active_kind == 'board' and connected:
            state += ' · 控制服务已连接'
        else:
            state += ' · 未建立板端控制会话'
        device_text.set(state)
    def devices():
        if closing: return
        refresh_devices()
        root.after(1000, devices)
    devices()
    reconnect()
    poll()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, help='连接已有 socket（禁用后端管理）')
    parser.add_argument('--rl', type=Path, default=Path(os.environ.get('DUCK_SIM_RL', str(Path(__file__).resolve().parents[2]/'microduck_rl'))))
    parser.add_argument('--headless', action='store_true', help='不打开 MuJoCo 3D 窗口')
    args = parser.parse_args()
    run_gui(args.socket, args.rl, not args.headless)


if __name__ == "__main__":
    main()
