"""Board credentials stored only in macOS Keychain, never in project files."""
import sys


class BoardCredentials:
    SERVICE = 'org.microduck.keyboard.board-login'

    def __init__(self, backend=None):
        self.backend = backend

    def _backend(self):
        if self.backend is None:
            if sys.platform != 'darwin':
                raise OSError('记住密码目前仅支持 macOS 钥匙串')
            try:
                # Explicit backend: never silently select a plaintext fallback.
                from keyring.backends.macOS import Keyring
                self.backend = Keyring()
            except ImportError:
                raise OSError('缺少 keyring，请安装 scripts/requirements-keyboard.txt') from None
        return self.backend

    def _account(self, port):
        if not port:
            raise OSError('请先选择开发板 USB 串口')
        return 'debian@' + port

    def get(self, port):
        account = self._account(port)
        backend = self._backend()
        try:
            return backend.get_password(self.SERVICE, account)
        except Exception:
            raise OSError('无法读取 macOS 钥匙串，请授权访问或取消记住密码后手动输入') from None

    def save(self, port, password):
        account = self._account(port)
        backend = self._backend()
        try:
            backend.set_password(self.SERVICE, account, password)
        except Exception:
            raise OSError('密码未保存：macOS 钥匙串拒绝访问或不可用') from None

    def forget(self, port):
        if self.get(port) is None:
            return
        try:
            self._backend().delete_password(self.SERVICE, self._account(port))
        except Exception:
            raise OSError('未能删除已保存密码，请检查钥匙串访问权限') from None
