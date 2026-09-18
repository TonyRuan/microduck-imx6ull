"""No board required: ownership, switching and transport safety regression tests."""
from concurrent.futures import Future
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from duck_backends import ManagedClient, Session, free_port
from duck_keyboard import Link, RpcError


class ManagementTests(unittest.TestCase):
    def test_switch_closes_old_before_start_and_drops_password(self):
        order = []
        class FakeSession:
            def __init__(self, kind, *args):
                self.kind, self.skills, self.directory = kind, set(), '/tmp/test'
                self.viewer = False
            def start(self, port, password): order.append(('start', self.kind))
            def close(self): order.append(('close', self.kind))
        with patch('duck_backends.Session', FakeSession):
            client = ManagedClient('/tmp/rl')
            client.select('board', '/dev/example', 'not-a-real-password')
            client.connect()
            self.assertEqual(client.profile[2], '')
            client.select('mac')
            client.connect()
            self.assertEqual(order, [('start','board'), ('close','board'), ('start','mac')])
            client.disconnect()
            self.assertIsNone(client.session)

    def test_failed_start_is_cleaned_and_never_ready(self):
        closed=[]
        class BrokenSession:
            def __init__(self,*args): pass
            def start(self,*args): raise OSError('login failed')
            def close(self): closed.append(True)
        with patch('duck_backends.Session', BrokenSession):
            client=ManagedClient('/tmp/rl')
            with self.assertRaisesRegex(OSError, 'login failed'): client.connect()
            self.assertTrue(closed)
            self.assertIsNone(client.active_kind)
            self.assertIsNone(client.session)

    def test_board_unsupported_skill_never_reaches_wire(self):
        client=ManagedClient('/tmp/rl')
        class NoCalls:
            def call(self,*args): raise AssertionError('must not be sent')
        client.session=NoCalls()
        with self.assertRaisesRegex(RpcError,'未加载'):
            client.call('robot.do', {'skill':'roulade'})

    def test_start_failure_does_not_attach_existing_listener(self):
        with tempfile.TemporaryDirectory(prefix='duck-test-',dir='/tmp') as directory:
            session=Session.__new__(Session)
            session.directory=Path(directory)
            session.rl=Path('/missing')
            session.viewer=False
            session.cancel=threading.Event()
            session.progress=lambda message:None
            class FailedChild:
                def poll(self):return 1
            def spawn(*args):
                (session.directory/'body.log').write_text('Address already in use')
                return FailedChild()
            session.spawn=spawn
            with socket.socket() as foreign:
                foreign.bind(('127.0.0.1',0));foreign.listen()
                with patch('duck_backends.free_port',return_value=foreign.getsockname()[1]):
                    with self.assertRaisesRegex(OSError,'Address already in use'):
                        session.start_body()
                foreign.settimeout(.02)
                with self.assertRaises(socket.timeout): foreign.accept()

    def test_no_display_uses_headless_body(self):
        with tempfile.TemporaryDirectory(prefix='duck-display-', dir='/tmp') as directory:
            session=Session.__new__(Session)
            session.directory=Path(directory)
            session.rl=Path('/example')
            session.viewer=True
            session.cancel=threading.Event()
            session.progress=lambda message:None
            calls=[]
            class Child:
                def poll(self):return None
            def spawn(argv,*args):
                calls.append(argv)
                (session.directory/'body.log').write_text(f'robotd --sim 127.0.0.1:{session.port}')
                return Child()
            session.spawn=spawn
            with patch('duck_backends.display_available', return_value=False):session.start_body()
            self.assertFalse(session.viewer)
            self.assertEqual(calls[0][0], '/example/.venv/bin/python')
            self.assertIn('--headless', calls[0])

    def test_link_switch_clears_held_motion(self):
        class Client:
            def __init__(self):self.connects=0;self.moves=[]
            def connect(self):self.connects+=1
            def disconnect(self):pass
            def call(self,*args):return {'healthy':True,'control_loop':{}}
            def move(self,vx=0,vyaw=0):self.moves.append((self.connects,vx,vyaw))
        c=Client(); link=Link(c);link.start();link.connect_requested.set()
        try:
            until=time.monotonic()+2
            while c.connects<1 and time.monotonic()<until:time.sleep(.01)
            link.update(.4,0)
            time.sleep(.08)
            link.connect_requested.set()
            until=time.monotonic()+2
            while not any(n==2 for n,_,_ in c.moves) and time.monotonic()<until:time.sleep(.01)
            self.assertTrue(any(n==2 for n,_,_ in c.moves))
            self.assertTrue(all(v==0 and w==0 for n,v,w in c.moves if n==2))
        finally:
            link.done.set();link.join(timeout=2)


class BridgeTests(unittest.TestCase):
    def test_expired_move_is_not_replayed_and_shutdown_is_pumped(self):
        serial, board=socket.socketpair();sim,body=socket.socketpair()
        class Shell:
            fd=serial.fileno()
            pending=b''
            def send(self,data):serial.sendall(data)
        class Child:
            def poll(self):return None
        session=Session.__new__(Session)
        session.shell=Shell();session.sim=sim;session.body=Child()
        session.stopping=threading.Event();session.requests=queue.Queue();session.failure=None
        expired=Future()
        session.requests.put(('robot.move',{'vx':.4},time.monotonic()-1,expired))
        thread=threading.Thread(target=session.bridge)
        thread.start()
        try:
            with self.assertRaisesRegex(OSError,'过期'):expired.result(timeout=1)
            session.stopping.set()
            board.settimeout(1)
            self.assertEqual(board.recv(100),b'S\n')
            board.sendall(b'B\t{"op":"read"}\n')
            body.settimeout(1)
            self.assertEqual(body.recv(100),b'{"op":"read"}\n')
            body.sendall(b'{"ok":true}\n')
            self.assertEqual(board.recv(100),b'B\t{"ok":true}\n')
            board.sendall(b'R\tstopped\n')
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertIsNone(session.failure)
        finally:
            board.close();body.close();thread.join(timeout=2);serial.close();sim.close()


if __name__=='__main__':unittest.main()
