"""Credential tests never access the real keychain or a physical board."""
import unittest
from unittest.mock import patch

from duck_credentials import BoardCredentials
from duck_backends import ManagedClient


class MemoryKeychain:
    def __init__(self): self.values = {}
    def get_password(self, service, account): return self.values.get((service, account))
    def set_password(self, service, account, password): self.values[service, account] = password
    def delete_password(self, service, account): del self.values[service, account]


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryKeychain()
        self.store = BoardCredentials(self.backend)

    def test_roundtrip_separate_boards_and_forget(self):
        self.store.save('/dev/board-a', 'test-a')
        self.store.save('/dev/board-b', 'test-b')
        self.assertEqual(self.store.get('/dev/board-a'), 'test-a')
        self.store.forget('/dev/board-a')
        self.store.forget('/dev/board-a')
        self.assertIsNone(self.store.get('/dev/board-a'))
        self.assertEqual(self.store.get('/dev/board-b'), 'test-b')

    def test_errors_never_reveal_secret(self):
        with patch.object(self.backend, 'set_password', side_effect=RuntimeError('secret-value')):
            with self.assertRaises(OSError) as raised:
                self.store.save('/dev/test', 'secret-value')
            self.assertNotIn('secret-value', str(raised.exception))

    def test_saved_password_survives_new_client(self):
        calls = []
        class FakeSession:
            def __init__(self, *args):
                self.skills, self.viewer, self.directory = set(), False, '/tmp/test'
            def start(self, port, password): calls.append((port, password))
            def close(self): pass
        with patch('duck_backends.Session', FakeSession):
            first = ManagedClient('/rl', credentials=self.store)
            first.select('board', '/dev/test', 'example', remember=True)
            first.connect()
            first.disconnect()
            second = ManagedClient('/rl', credentials=self.store)
            second.select('board', '/dev/test', remember=True)
            second.connect()
            self.assertEqual(calls, [('/dev/test', 'example'), ('/dev/test', 'example')])
            self.assertEqual(second.profile[2], '')
            second.disconnect()

    def test_failed_login_does_not_replace_saved_password(self):
        self.store.save('/dev/test', 'working')
        class Broken:
            def __init__(self, *args): pass
            def start(self, *args): raise OSError('login failed')
            def close(self): pass
        with patch('duck_backends.Session', Broken):
            client = ManagedClient('/rl', credentials=self.store)
            client.select('board', '/dev/test', 'bad', remember=True)
            with self.assertRaises(OSError): client.connect()
            self.assertEqual(self.store.get('/dev/test'), 'working')

    def test_unchecked_does_not_load_or_save(self):
        self.store.save('/dev/test', 'working')
        client = ManagedClient('/rl', credentials=self.store)
        client.select('board', '/dev/test', remember=False)
        with patch.object(self.store, 'get', side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(OSError, '请输入'): client.connect()

    def test_save_denied_keeps_live_connection_and_warns(self):
        class Session:
            def __init__(self, *args):
                self.skills, self.viewer, self.directory = set(), False, '/tmp/test'
            def start(self, *args): pass
            def close(self): pass
        with patch('duck_backends.Session', Session), patch.object(self.store, 'save', side_effect=OSError('保存失败')):
            client = ManagedClient('/rl', credentials=self.store)
            client.select('board', '/dev/test', 'example', remember=True)
            client.connect()
            self.assertEqual(client.active_kind, 'board')
            self.assertEqual(client.events.get(), ('credential', '保存失败'))
            client.disconnect()


if __name__ == '__main__': unittest.main()
