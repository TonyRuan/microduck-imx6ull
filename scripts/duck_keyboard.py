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


SKILLS = {"ctrl": "sit_toggle", "space": "roulade", "j": "kick_left", "k": "kick_right"}
KEYS = {"w", "a", "s", "d", *SKILLS}
TICK = 0.05
LEASE = 0.25
# Command limits, not guaranteed physical speeds. See simulation.md for the
# alpha_walking measurements behind the default and the low-speed warning.
SPEED_LIMITS = (0.40, 0.40, 1.0)
SPEED_PRESETS = {"仿真实测": (0.30, 0.40, 0.40), "低速试探": (0.10, 0.08, 0.40)}


def speed_hint(forward, backward):
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
        try:
            while not self.done.is_set():
                started = time.monotonic()
                try:
                    if self.connect_requested.is_set():
                        self.connect_requested.clear()
                        self.stop_motion()
                        self.client.call("robot.health")
                        connected = True
                        self.events.put(("connected", "已连接 · 点击本窗口后使用键盘"))
                    if connected:
                        vx, vyaw, skill = self.next_command(time.monotonic())
                        self.client.move(0.0 if skill else vx, 0.0 if skill else vyaw)
                        if skill:
                            self.client.call("robot.do", {"skill": skill})
                            self.events.put(("action", f"已接受动作：{skill}"))
                except RpcError as error:
                    self.stop_motion()
                    self.events.put(("refused", str(error)))
                except (OSError, ValueError, TypeError) as error:
                    connected = False
                    self.stop_motion()
                    self.events.put(("disconnected", f"未连接：{error} · 启动仿真后点重新连接"))
                self.done.wait(max(0.0, TICK - (time.monotonic() - started)))
        finally:
            if connected:
                try:
                    self.client.move()
                except (OSError, ValueError, RpcError):
                    pass


def run_gui(path):
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as error:
        raise SystemExit("需要 Tk GUI 支持。macOS：brew install python-tk@3.12；然后使用 python3.12 启动。") from error

    root = tk.Tk()
    root.title("Microduck · 键盘遥控")
    root.geometry("620x860")
    root.minsize(620, 850)
    root.configure(bg="#eff4f2")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", background="#eff4f2", foreground="#183e36", padding=7)
    style.map("TButton", background=[("active", "#c4edb9")])
    style.configure("Horizontal.TScale", background="#eff4f2", troughcolor="#d4dfda")
    controls = Controls()
    link = Link(RobotClient(path))
    connected = False
    pending_release = {}
    labels = {}
    status = tk.StringVar(value="正在连接本地仿真…")
    focus_text = tk.StringVar(value="点击此窗口启用键盘")
    velocity = tk.StringVar(value="0.00 m/s   ·   0.00 rad/s")
    action_text = tk.StringVar(value="动作按一次触发一次；执行动作前会清除行走输入。")
    default_speeds = SPEED_PRESETS["仿真实测"]
    forward, backward, turn = [tk.DoubleVar(value=value) for value in default_speeds]
    speed_text = tk.StringVar(value=speed_hint(forward.get(), backward.get()))

    def text(parent, value=None, variable=None, size=13, color="#183e36", **kwargs):
        return tk.Label(parent, text=value, textvariable=variable, bg=parent.cget("bg"),
                        fg=color, font=("Helvetica", size), **kwargs)

    main = tk.Frame(root, bg="#eff4f2", padx=26, pady=14)
    main.pack(fill="both", expand=True)
    text(main, "MICRODUCK  /  TELEOP", size=11, color="#537a6c", anchor="w").pack(fill="x")
    text(main, "键盘遥控", size=28, anchor="w").pack(fill="x", pady=(5, 8))
    text(main, variable=status, size=12, anchor="w", wraplength=550, justify="left").pack(fill="x")
    text(main, variable=focus_text, size=12, color="#648078", anchor="w").pack(fill="x", pady=(4, 8))

    pad = tk.Frame(main, bg="white", padx=20, pady=10)
    pad.pack(fill="x")
    pad.columnconfigure((0, 1, 2), weight=1)
    for key, caption, row, col in [("w", "W  前进", 0, 1), ("a", "A  左转", 1, 0),
                                    ("s", "S  后退", 1, 1), ("d", "D  右转", 1, 2)]:
        label = tk.Label(pad, text=caption, bg="#eff4f2", fg="#183e36", pady=12,
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
        row.pack(fill="x", pady=5)
        text(row, title, size=13).pack(side="left")
        value_label = text(row, f"{variable.get():.2f} {unit}", size=12, width=12, anchor="e")
        value_label.pack(side="right")
        def changed(*_, variable=variable, label=value_label, suffix=unit):
            label.config(text=f"{variable.get():.2f} {suffix}")
            speed_text.set(speed_hint(forward.get(), backward.get()))
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
        for variable, value in zip((forward, backward, turn), SPEED_PRESETS[name]):
            variable.set(value)
        action_text.set(f"已选 {name} · 重新按方向键移动")

    for name in SPEED_PRESETS:
        button = ttk.Button(presets, text=name, command=lambda name=name: apply_preset(name))
        button.pack(side="left", padx=(0, 8))
        # Space belongs to the roll action everywhere in the panel; Enter keeps
        # the preset buttons accessible without changing that control contract.
        button.bind("<Return>", lambda event, name=name: apply_preset(name))
    text(main, variable=speed_text, size=11, color="#906020", wraplength=560,
         justify="left", anchor="w").pack(fill="x", pady=(6, 2))
    text(main, "以上为指令速度，并非实测速度或硬件极限。", size=11,
         color="#648078", anchor="w").pack(fill="x", pady=(0, 8))

    actions = tk.Frame(main, bg="white", padx=15, pady=12)
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
            link.skill(skill)
            action_text.set(f"正在请求：{skill}")

    for index, (key, title) in enumerate([("ctrl", "Ctrl  坐下 / 站起"), ("space", "空格  向前翻滚"),
                                         ("j", "J  左脚踢球"), ("k", "K  右脚踢球")]):
        ttk.Button(actions, text=title, takefocus=False, command=lambda key=key: perform(SKILLS[key])).grid(
            row=index // 2, column=index % 2, sticky="ew", padx=5, pady=5)
    text(main, variable=action_text, size=11, wraplength=550, justify="left", anchor="w").pack(fill="x", pady=10)
    footer = tk.Frame(main, bg="#eff4f2")
    footer.pack(fill="x")

    def stop():
        controls.block_movement()
        link.stop_motion()
        action_text.set("已停止行走 · 已接受的翻滚 / 踢球由策略完成")

    def reconnect():
        nonlocal connected
        clear()
        connected = False
        status.set("正在重新连接…")
        link.connect_requested.set()

    ttk.Button(footer, text="停止行走  Esc", command=stop, takefocus=False).pack(side="left")
    ttk.Button(footer, text="重新连接", command=reconnect, takefocus=False).pack(side="right")
    text(main, "松开方向键即停止；切换窗口自动停止。\n只在本窗口接收按键，可组合 W+A / W+D 转弯。", size=11,
         color="#648078", justify="left", anchor="w").pack(fill="x", pady=(14, 4))
    text(main, str(path), size=10, color="#648078", wraplength=550, anchor="w").pack(fill="x")

    def normalized(event):
        return "ctrl" if event.keysym in {"Control_L", "Control_R"} else event.keysym.lower()

    def keydown(event):
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
        nonlocal connected
        while not link.events.empty():
            kind, message = link.events.get()
            if kind in {"connected", "disconnected"}:
                clear()
                connected = kind == "connected"
                status.set(message)
            else:
                if kind == "refused":
                    clear()
                action_text.set(message)
        focused = root.focus_displayof() is not None
        if not focused:
            clear()
        vx, vyaw = controls.twist(forward.get(), backward.get(), turn.get()) if connected and focused else (0.0, 0.0)
        link.update(vx, vyaw)
        velocity.set(f"{vx:+.2f} m/s   ·   {vyaw:+.2f} rad/s")
        focus_text.set("键盘已就绪 · 按住方向键移动" if focused and connected else "键盘暂停 · 请连接并点击本窗口")
        for key, label in labels.items():
            label.configure(bg="#c4edb9" if key in controls.pressed - controls.blocked else "#eff4f2")
        root.after(50, poll)

    def close():
        clear()
        if modifier_monitor is not None:
            NSEvent.removeMonitor_(modifier_monitor)
        link.done.set()
        link.join(timeout=0.5)
        root.destroy()

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
                    if connected and root.focus_displayof() is not None:
                        skill = controls.press("ctrl")
                        if skill:
                            perform(skill)
                elif not down:
                    controls.release("ctrl")
                ctrl_was_down = down
            root.after(10, modifiers)

        modifiers()
    link.start()
    link.connect_requested.set()
    poll()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    state = Path(os.environ.get("DUCK_SIM_STATE", str(Path.home() / ".cache/duck-sim")))
    duck = os.environ.get("DUCK_SIM_DUCK", "duck-a")
    parser.add_argument("--socket", type=Path, default=state / f"{duck}.sock")
    args = parser.parse_args()
    run_gui(args.socket)


if __name__ == "__main__":
    main()
