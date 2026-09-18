"""Owned simulator/control sessions for the keyboard panel. No physical motor I/O.

All blocking work runs on Link's worker or the serial pump, never on Tk's thread.
Every session owns its children; existing duck-sim instances are never attached.
"""
from concurrent.futures import Future
import glob
import json
import os
from pathlib import Path
import queue
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/imx6ull-policy"
sys.path.insert(0, str(EXPERIMENT))
from serial_shell import SerialShell


def board_ports():
    return sorted(glob.glob('/dev/cu.usbmodem*'))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def display_available():
    if sys.platform != 'darwin':
        return True
    # NSScreen can still list a sleeping display. GLFW needs an active display
    # and can segfault instead of raising when the active list is empty.
    import ctypes
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    displays = (ctypes.c_uint32 * 32)()
    count = ctypes.c_uint32()
    result = cg.CGGetActiveDisplayList(32, displays, ctypes.byref(count))
    return result == 0 and count.value > 0


def stop_child(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def socket_call(path, method, params=None):
    from duck_keyboard import RobotClient
    return RobotClient(path).call(method, params)


class Session:
    def __init__(self, kind, rl, viewer, cancel, progress):
        self.kind, self.rl, self.viewer = kind, Path(rl), viewer
        self.cancel, self.progress = cancel, progress
        self.directory = Path(tempfile.mkdtemp(prefix='duck-teleop-'))
        self.body = self.daemon = self.sim = self.shell = self.pump = None
        self.logs = []
        self.requests = queue.Queue(maxsize=4)
        self.stopping = threading.Event()
        self.failure = None
        self.sock = self.directory / 'robot.sock'
        self.skills = set()
        self.truth = None

    def check(self):
        if self.cancel.is_set():
            raise OSError('连接已取消')

    def spawn(self, argv, name, env=None):
        log = (self.directory / f'{name}.log').open('w')
        self.logs.append(log)
        return subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)

    def start_body(self):
        if self.viewer and not display_available():
            self.viewer = False
            self.progress('当前没有活动显示器，使用无 3D 窗口仿真')
        self.progress('启动独立 MuJoCo 仿真…')
        self.port = free_port()
        python = self.rl / '.venv/bin' / ('mjpython' if self.viewer and sys.platform == 'darwin' else 'python')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(self.rl / 'src') + os.pathsep + env.get('PYTHONPATH', '')
        argv = [str(python), '-m', 'mjlab_microduck.sim.body_server', '--port', str(self.port), '--keyframe', 'HOME']
        if not self.viewer:
            argv.append('--headless')
        elif sys.platform == 'darwin':
            # Process-local Cocoa defaults avoid the crash-recovery modal blocking
            # GLFW before Python starts. Do not alter the user's saved preferences.
            body_args = ['body_server', '--port', str(self.port), '--keyframe', 'HOME']
            bootstrap = ('import runpy,sys;sys.argv=' + repr(body_args) +
                         ';runpy.run_module("mjlab_microduck.sim.body_server",run_name="__main__")')
            argv = [str(python), '-c', bootstrap, '-ApplePersistenceIgnoreState', 'YES',
                    '-NSQuitAlwaysKeepsWindows', 'NO']
        self.body = self.spawn(argv, 'body', env)
        deadline = time.monotonic() + 20
        # The child prints its address only AFTER binding. Never accept an existing
        # listener just because connect() succeeds (the old HIL harness did that).
        while time.monotonic() < deadline:
            self.check()
            log = (self.directory / 'body.log').read_text()
            if self.body.poll() is not None:
                raise OSError('模拟器启动失败：' + log[-600:])
            if f'robotd --sim 127.0.0.1:{self.port}' in log:
                return
            time.sleep(.05)
        raise OSError('模拟器未就绪；日志：' + str(self.directory / 'body.log'))

    def start_mac(self):
        self.progress('启动 Mac robotd / ONNX Runtime…')
        binary = ROOT / 'target/debug/robotd'
        if not binary.exists():
            raise OSError('缺少 Mac robotd，请先运行 cargo build -p robotd')
        policies = Path(os.environ.get('DUCK_TELEOP_POLICIES', str(Path.home()/'.cache/duck-sim/policies/current')))
        walk = EXPERIMENT / 'out/velstand.onnx'
        if not walk.exists():
            walk = policies / 'velstand.onnx'
        if not walk.exists():
            raise OSError('缺少 velstand.onnx，请按 HIL.md 准备模型')
        config = '[policy]\nenabled = true\nwalk = ' + json.dumps(str(walk)) + '\nstand = "none"\n'
        for slot, filename, skill in [
            ('sitstand', 'alpha_sitstand.onnx', 'sit_toggle'),
            ('ground_pick', 'alpha_ground_pick.onnx', 'ground_pick'),
            ('kick_left', 'ball_kick_left.onnx', 'kick_left'),
            ('kick_right', 'ball_kick_right.onnx', 'kick_right'),
            ('roulade', 'roulade.onnx', 'roulade'),
        ]:
            model = policies / filename
            config += f'{slot} = ' + json.dumps(str(model) if model.exists() else 'none') + '\n'
            if model.exists():
                self.skills.add(skill)
        config += '\n[audio]\nenabled = false\npet_detect = false\n[chorale]\naccept = false\n'
        params = self.directory / 'robotd.toml'
        params.write_text(config)
        env = os.environ.copy()
        if 'ORT_DYLIB_PATH' not in env:
            candidates = list((self.rl/'.venv/lib').glob('python*/site-packages/onnxruntime/capi/libonnxruntime*.dylib'))
            if not candidates:
                candidates = list((self.rl/'.venv/lib').glob('python*/site-packages/onnxruntime/capi/libonnxruntime.so*'))
            if not candidates:
                raise OSError('RL venv 缺少 ONNX Runtime 动态库')
            env['ORT_DYLIB_PATH'] = str(candidates[0])
        self.daemon = self.spawn([str(binary), '--sim', f'127.0.0.1:{self.port}',
                                  '--params', str(params), '--socket', str(self.sock)], 'robotd', env)

    def start_board(self, port, password):
        if not port or not Path(port).exists():
            raise OSError('未发现所选开发板 USB 串口')
        self.progress('登录开发板（USB 串口）…')
        self.shell = SerialShell(password, port=port, cancel=self.cancel)
        if 'BUSY' in self.shell.command("awk '$2 ~ /:1E8B$/ && $4 == \"0A\" {print \"BUSY\"}' /proc/net/tcp"):
            raise OSError('开发板 7819 端口正被使用；请先停止其他 HIL 会话')
        self.progress('校验并部署 ARM 控制程序和步态模型…')
        board = '/home/debian/microduck-policy-bench/gui'
        self.shell.command(f'mkdir -p {board}')
        import hashlib
        for source, dest in [(EXPERIMENT/'out/robotd-hil', 'robotd-hil'),
                             (EXPERIMENT/'out/hil-proxy', 'hil-proxy'),
                             (EXPERIMENT/'out/weights.bin', 'velstand.duckmlp')]:
            self.check()
            if not source.exists():
                raise OSError(f'缺少 {source.name}，请先按 HIL.md 构建')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest not in self.shell.command(f'sha256sum {board}/{dest} 2>/dev/null'):
                self.shell.upload(source, f'{board}/{dest}')
        params = self.directory / 'robotd.toml'
        params.write_text((EXPERIMENT/'robotd-hil.toml').read_text().replace(
            '/home/debian/microduck-policy-bench/velstand.duckmlp', board+'/velstand.duckmlp'))
        self.shell.upload(params, board+'/robotd.toml')
        self.shell.command(f'chmod +x {board}/robotd-hil {board}/hil-proxy')
        self.sim = socket.create_connection(('127.0.0.1', self.port), timeout=1)
        self.sim.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.progress('建立开发板控制闭环…')
        self.shell.send((f'cd {board}; gui_tty=$(stty -g); stty raw -echo; '
                         './hil-proxy ./robotd-hil robotd.toml ./robot.sock; '
                         'stty "$gui_tty"; printf "\\nGUI_DONE\\n"\r').encode())
        self.tunneled = True
        self.shell.until(b'R\tready\n', timeout=10)
        self.shell.io_timeout = 1
        self.pump = threading.Thread(target=self.bridge, daemon=True)
        self.pump.start()

    def bridge(self):
        serial_buffer, self.shell.pending = self.shell.pending, b''
        sim_buffer = b''
        pending = {}
        identifier = 0
        stop_at = None
        try:
            while True:
                now = time.monotonic()
                if self.stopping.is_set() and stop_at is None:
                    stop_at = now
                    self.shell.send(b'S\n')
                if stop_at and now-stop_at > 8:
                    self.shell.send(b'Q\n')
                    raise OSError('板端退出超时')
                if self.body.poll() is not None:
                    raise OSError('模拟器已退出')
                while not self.requests.empty():
                    method, params, expires, future = self.requests.get_nowait()
                    if future.cancelled() or now >= expires or stop_at:
                        if not future.done(): future.set_exception(OSError('过期控制指令已丢弃'))
                        continue
                    identifier += 1
                    if not future.set_running_or_notify_cancel():
                        continue
                    pending[identifier] = (future, expires)
                    request = dict(jsonrpc='2.0', id=identifier, method=method, params=params or {})
                    self.shell.send(b'I\t'+json.dumps(request,separators=(',', ':')).encode()+b'\n')
                for key, (future, expires) in list(pending.items()):
                    if now >= expires:
                        pending.pop(key)
                        if not future.done(): future.set_exception(OSError('开发板 RPC 超时'))
                ready, _, _ = select.select([self.shell.fd, self.sim], [], [], .005)
                if self.shell.fd in ready:
                    data = os.read(self.shell.fd, 65536)
                    if not data: raise OSError('开发板 USB 已断开')
                    serial_buffer += data
                while b'\n' in serial_buffer:
                    line, serial_buffer = serial_buffer.split(b'\n', 1)
                    if line.startswith(b'B\t'):
                        self.sim.sendall(line[2:]+b'\n')
                    elif line.startswith(b'I\t'):
                        answer = json.loads(line[2:])
                        entry = pending.pop(answer.get('id'), None)
                        if entry and not entry[0].done(): entry[0].set_result(answer)
                    elif line == b'R\tstopped':
                        self.shell.pending = serial_buffer
                        return
                    elif line.startswith(b'E\t'):
                        for future, _ in pending.values():
                            if not future.done(): future.set_exception(OSError(line[2:].decode()))
                        pending.clear()
                    elif line:
                        raise OSError('USB 控制协议异常')
                if self.sim in ready:
                    data = self.sim.recv(65536)
                    if not data: raise OSError('模拟器连接已断开')
                    sim_buffer += data
                while b'\n' in sim_buffer:
                    line, sim_buffer = sim_buffer.split(b'\n', 1)
                    answer = json.loads(line)
                    if 'trunk' in answer: self.truth = answer['trunk']
                    self.shell.send(b'B\t'+line+b'\n')
                if len(serial_buffer)>131072 or len(sim_buffer)>131072:
                    raise OSError('控制报文超限')
        except (OSError, ValueError) as error:
            self.failure = str(error)
        finally:
            for future, _ in pending.values():
                if not future.done(): future.set_exception(OSError(self.failure or '后端已停止'))

    def call(self, method, params=None):
        from duck_keyboard import RpcError
        if self.body and self.body.poll() is not None:
            raise OSError('模拟器已退出')
        if self.kind == 'mac':
            return socket_call(self.sock, method, params)
        if self.failure or not self.pump or not self.pump.is_alive():
            raise OSError(self.failure or '开发板控制桥未连接')
        future = Future()
        try:
            self.requests.put_nowait((method, params, time.monotonic()+.2, future))
        except queue.Full:
            raise OSError('控制队列已满') from None
        try:
            answer = future.result(timeout=.25)
        except TimeoutError:
            future.cancel()
            raise OSError('开发板响应超时') from None
        if 'error' in answer:
            raise RpcError(answer['error'].get('message', 'RPC error'))
        result = answer.get('result', {})
        if result.get('accepted') is False:
            raise RpcError(result.get('reason', '动作被拒绝'))
        return result

    def start(self, port='', password=''):
        self.start_body()
        if self.kind == 'mac': self.start_mac()
        else: self.start_board(port, password)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            self.check()
            try:
                health = self.call('robot.health')
                if health.get('healthy') and health.get('imu', {}).get('ready'):
                    self.call('robot.enable', {'on': True})
                    self.progress('控制已启用，等待启动回零完成…')
                    for _ in range(30):
                        self.check()
                        time.sleep(.1)
                    return
            except (OSError, ValueError):
                if self.daemon and self.daemon.poll() is not None:
                    raise OSError('robotd 启动失败，查看 '+str(self.directory/'robotd.log'))
            time.sleep(.1)
        raise OSError('控制器健康检查未通过')

    def close(self):
        try:
            self.call('robot.move', dict(vx=0.,vy=0.,vyaw=0.))
        except Exception:
            pass
        self.stopping.set()
        if self.shell: self.shell.cancel = None  # cleanup must survive cancellation
        if self.pump:
            self.pump.join(timeout=10)
            if self.pump.is_alive():
                raise OSError('USB 桥尚未退出，拒绝启动另一后端')
        if self.shell:
            try:
                if getattr(self, 'tunneled', False):
                    if self.failure or not self.pump: self.shell.send(b'Q\n')
                    self.shell.until(b'GUI_DONE\r\n', timeout=5)
                    self.shell.until(b'__DUCK_PROMPT__ ', timeout=2)
                self.shell.close()
            except (OSError, ValueError):
                # A pulled cable cannot accept shell restoration. Still release the FD.
                try: os.close(self.shell.fd)
                except OSError: pass
            self.shell = None
        if self.sim: self.sim.close()
        stop_child(self.daemon)
        stop_child(self.body)
        for log in self.logs: log.close()


class ManagedClient:
    """The GUI only changes the desired profile; connect/close run on Link."""
    def __init__(self, rl, viewer=True, credentials=None):
        from duck_credentials import BoardCredentials
        self.credentials = credentials if credentials is not None else BoardCredentials()
        self.remember = False
        self.rl, self.viewer = rl, viewer
        self.session = None
        self.profile = ('mac', '', '')
        self.cancel = threading.Event()
        self.events = queue.SimpleQueue()
        self.skills = set()
        self.active_kind = None

    def select(self, kind, port='', password='', remember=False):
        if kind not in {'mac', 'board'}: raise ValueError('unknown backend')
        self.profile = (kind, port, password)
        self.remember = remember

    def connect(self):
        self.disconnect()
        kind, port, password = self.profile
        self.profile = (kind, port, '')  # never retain the supplied password after login
        try:
            if kind == 'board' and self.remember and not password:
                self.events.put(('progress', '读取 macOS 钥匙串中的开发板密码…'))
                password = self.credentials.get(port)
            if kind == 'board' and not password:
                raise OSError('未找到已保存密码，请输入开发板密码再连接')
            self.session = Session(kind, self.rl, self.viewer, self.cancel,
                                   lambda message: self.events.put(('progress', message)))
            self.session.start(port, password)
            if kind == 'board' and self.remember:
                # Failed authentication never overwrites a working saved credential.
                try:
                    self.credentials.save(port, password)
                    self.events.put(('credential', '密码已记住：macOS 钥匙串（按 USB 串口区分）'))
                except OSError as error:
                    self.events.put(('credential', str(error)))
            self.active_kind = kind
            self.skills = self.session.skills.copy()
            viewing = '3D 窗口' if self.session.viewer else '无 3D 窗口（重连可恢复）'
            self.events.put(('ready', f'{"Mac / ONNX" if kind=="mac" else "i.MX6ULL / FP32"} · velstand · {viewing} · 日志 {self.session.directory}'))
        except Exception as error:
            self.disconnect()
            raise OSError(str(error)) from error

    def disconnect(self):
        if self.session:
            self.session.close()
            self.session = None
        self.active_kind = None
        self.skills = set()

    def call(self, method, params=None):
        from duck_keyboard import RpcError
        if not self.session: raise OSError('后端未连接')
        if method == 'robot.do' and (params or {}).get('skill') not in self.skills:
            raise RpcError('当前后端未加载此动作模型')
        return self.session.call(method, params)

    def move(self, vx=0., vyaw=0.):
        return self.call('robot.move', dict(vx=vx, vy=0., vyaw=vyaw))
