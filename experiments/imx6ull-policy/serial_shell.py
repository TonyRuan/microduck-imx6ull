"""Small macOS USB-gadget shell transport; never uses a motor UART.

Set MICRODUCK_BOARD_PASSWORD in the environment or enter it at the prompt.
Uploads are binary, SHA-256 checked, and confined to an explicitly named path.
"""
import argparse
import getpass
import hashlib
import os
import select
import shlex
import termios
import time


class SerialShell:
    def __init__(self, password, port="/dev/cu.usbmodem1234fire56783", user="debian"):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.original = termios.tcgetattr(self.fd)
        self.pending = b""
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B115200
        a[6][termios.VMIN] = a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        try:
            self.send(b"\r")
            self.until(b"login: ")
            self.send(user.encode() + b"\r")
            self.until(b"Password: ")
            self.send(password.encode() + b"\r")
            self.until((user + "@npi:~$ ").encode())
            self.send(b"export PS1='__DUCK_PROMPT__ '; stty -echo\r")
            self.until(b"\r\n__DUCK_PROMPT__ ")
        except BaseException:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
            os.close(self.fd)
            raise

    def send(self, data):
        end = time.monotonic() + 30
        while data:
            if time.monotonic() > end:
                raise TimeoutError("serial write")
            if select.select([], [self.fd], [], .2)[1]:
                try:
                    n = os.write(self.fd, data[:65536])
                    data = data[n:]
                except BlockingIOError:
                    pass

    def until(self, marker, timeout=30):
        end = time.monotonic() + timeout
        while marker not in self.pending:
            if time.monotonic() > end:
                raise TimeoutError(repr(self.pending[-1000:]))
            if select.select([self.fd], [], [], .2)[0]:
                self.pending += os.read(self.fd, 65536)
        i = self.pending.index(marker) + len(marker)
        result, self.pending = self.pending[:i], self.pending[i:]
        return result

    def command(self, command, timeout=30):
        self.send(command.encode() + b"\r")
        response = self.until(b"__DUCK_PROMPT__ ", timeout)
        return response.removesuffix(b"__DUCK_PROMPT__ ").decode(errors="replace")

    def upload(self, source, destination):
        data = source.read_bytes()
        if not data:
            raise ValueError("empty upload")
        dest = shlex.quote(destination)
        command = (
            "saved_tty=$(stty -g); stty raw -echo; printf 'UPLOAD_READY\\n'; "
            f"timeout 30 dd of={dest} bs={len(data)} count=1 iflag=fullblock status=none; "
            f"stty \"$saved_tty\"; sha256sum {dest}\r"
        )
        self.send(command.encode())
        self.until(b"UPLOAD_READY\n")
        self.send(data)
        result = self.until(b"__DUCK_PROMPT__ ").decode(errors="replace")
        if hashlib.sha256(data).hexdigest() not in result:
            raise ValueError("upload checksum mismatch: " + result)
        return hashlib.sha256(data).hexdigest()

    def close(self):
        try:
            self.send(b"stty echo\rexit\r")
            time.sleep(.2)
        finally:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
            os.close(self.fd)


def password():
    return os.environ.get("MICRODUCK_BOARD_PASSWORD") or getpass.getpass("Board password: ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    shell = SerialShell(password())
    try:
        print(shell.command(args.command, args.timeout))
    finally:
        shell.close()
