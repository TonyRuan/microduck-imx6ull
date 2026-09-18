"""Opt-in Tk check: DUCK_GUI_TEST=1 .keyboard-venv/bin/python -m unittest discover -s scripts -p test_duck_keyboard_gui.py"""
import os
import queue
import threading
import time
import unittest
from unittest.mock import patch

from duck_keyboard import run_gui


@unittest.skipUnless(os.environ.get('DUCK_GUI_TEST') == '1', 'requires a desktop Tk session')
class CompactGuiTests(unittest.TestCase):
    def test_compact_restores_layout_without_reconnecting(self):
        import tkinter as tk
        original_tk = tk.Tk
        errors = []
        class Client:
            def __init__(self):
                self.events = queue.SimpleQueue()
                self.cancel = threading.Event()
                self.active_kind = None
                self.policy_profile = 'velstand'
                self.skills = set()
                self.connects = self.closes = 0
                self.moves = []
            def select(self, *args, **kwargs): pass
            def connect(self): self.connects += 1; self.active_kind = 'mac'
            def disconnect(self): self.closes += 1
            def call(self, *args): return {'healthy': True, 'control_loop': {'achieved_hz': 50., 'missed': 0}}
            def move(self, vx=0., vyaw=0.): self.moves.append((vx, vyaw))
        client = Client()
        def widgets(w):
            yield w
            for child in w.winfo_children(): yield from widgets(child)
        def factory():
            root = original_tk()
            phase = 0
            original = None
            deadline = time.monotonic()+10
            def check():
                nonlocal phase, original
                try:
                    if time.monotonic() > deadline: raise AssertionError('GUI timed out')
                    buttons = {str(w.cget('text')): w for w in widgets(root) if w.winfo_class() == 'TButton'}
                    if phase == 0 and client.connects:
                        original = root.geometry()
                        self.assertTrue(buttons['P  捡拾 / 轮式蹲伏'].winfo_ismapped())
                        for w in widgets(root):
                            if w.winfo_ismapped():
                                self.assertLessEqual(w.winfo_rooty()+w.winfo_height(), root.winfo_rooty()+root.winfo_height())
                        root.focus_force()
                        root.event_generate('<KeyPress-w>')
                        phase = 1
                    elif phase == 1:
                        self.assertTrue(any(vx > 0 for vx, _ in client.moves))
                        buttons['缩小 · 迷你模式'].invoke()
                        phase = 2
                    elif phase == 2:
                        self.assertEqual((root.winfo_width(), root.winfo_height()), (340, 200))
                        self.assertTrue(root.attributes('-topmost'))
                        self.assertEqual(client.moves[-1], (0., 0.))
                        self.assertTrue(buttons['停止行走 · Esc'].winfo_ismapped())
                        for w in widgets(root):
                            if w.winfo_ismapped():
                                self.assertLessEqual(w.winfo_rooty()+w.winfo_height(), root.winfo_rooty()+200)
                        root.focus_force()
                        # OS repeats of the key held before shrinking must not
                        # restart walking until a real release/press pair.
                        root.event_generate('<KeyPress-w>')
                        phase = 3
                    elif phase == 3:
                        self.assertEqual(client.moves[-1], (0., 0.))
                        root.event_generate('<KeyRelease-w>')
                        root.after(60, lambda: root.event_generate('<KeyPress-w>'))
                        phase = 4
                    elif phase == 4:
                        self.assertGreater(client.moves[-1][0], 0)
                        buttons['停止行走 · Esc'].invoke()
                        phase = 5
                    elif phase == 5:
                        self.assertEqual(client.moves[-1], (0., 0.))
                        buttons['展开 ↗'].invoke()
                        phase = 6
                    elif phase == 6:
                        self.assertEqual(root.geometry(), original)
                        self.assertFalse(root.attributes('-topmost'))
                        self.assertEqual(client.connects, 1)
                        self.assertEqual(client.closes, 0)
                        self.assertEqual(client.moves[-1], (0., 0.))
                        root.tk.call(root.protocol('WM_DELETE_WINDOW'))
                        return
                except Exception as error:
                    errors.append(error)
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))
                    return
                root.after(200, check)
            root.after(300, check)
            return root
        with patch('tkinter.Tk', factory), patch('duck_backends.ManagedClient', return_value=client):
            run_gui(viewer=False)
        self.assertEqual(errors, [])
        self.assertEqual(client.closes, 1)
