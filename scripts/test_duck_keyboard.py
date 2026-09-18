"""Run with: python3 -m unittest discover -s scripts -p test_duck_keyboard.py"""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from duck_keyboard import (Controls, LEASE, Link, RobotClient, RpcError, SKILLS,
                           SPEED_LIMITS, SPEED_PRESETS, speed_hint)


class InputTests(unittest.TestCase):
    def test_speed_presets_and_reverse_limit(self):
        self.assertEqual(SPEED_LIMITS, (.4, .4, 1.0))
        for speeds in SPEED_PRESETS.values():
            for value, limit in zip(speeds, SPEED_LIMITS):
                self.assertTrue(0 <= value <= limit)
        controls = Controls()
        controls.press("s")
        self.assertEqual(controls.twist(*SPEED_PRESETS["仿真实测"]), (-.4, 0))
        # Changing a preset must not accelerate an already held direction key.
        controls.block_movement()
        self.assertEqual(controls.twist(*SPEED_PRESETS["仿真实测"]), (0, 0))
        controls.release("s")
        controls.press("s")
        self.assertEqual(controls.twist(*SPEED_PRESETS["仿真实测"]), (-.4, 0))

    def test_low_speed_hint_and_drift_warning(self):
        for forward, backward in [(.1, .4), (.3, .08), (.3, .3), (.3, .39)]:
            self.assertIn("低速可能不迈步", speed_hint(forward, backward))
        self.assertIn("侧偏", speed_hint(.3, .4))
        self.assertNotIn("低速可能不迈步", speed_hint(0, 0))
        self.assertIn('velstand', speed_hint(.4, .4, 'velstand'))
        self.assertNotIn('alpha', speed_hint(.1, .08, 'velstand'))

    def test_motion_and_opposites(self):
        controls = Controls()
        controls.press("w")
        controls.press("a")
        self.assertEqual(controls.twist(.1, .08, .4), (.1, .4))
        controls.press("s")
        controls.press("d")
        self.assertEqual(controls.twist(.1, .08, .4), (0, 0))
        controls.release("w")
        controls.release("a")
        self.assertEqual(controls.twist(.1, .08, .4), (-.08, -.4))
        controls.clear()
        self.assertEqual(controls.twist(.1, .08, .4), (0, 0))

    def test_single_shot_and_release_to_rearm(self):
        for key, skill in SKILLS.items():
            controls = Controls()
            self.assertEqual(controls.press(key), skill)
            for _ in range(20):
                self.assertIsNone(controls.press(key))
            controls.release(key)
            self.assertEqual(controls.press(key), skill)

    def test_skill_cancels_held_walk_until_release(self):
        controls = Controls()
        controls.press("w")
        controls.press("space")
        controls.press("w")  # OS repeat must not restart walking after a trick.
        self.assertEqual(controls.twist(.1, .08, .4), (0, 0))
        controls.release("w")
        controls.press("w")
        self.assertEqual(controls.twist(.1, .08, .4), (.1, 0))

    def test_watchdog_and_stale_actions(self):
        link = Link(None)
        link.update(.1, .4)
        self.assertEqual(link.next_command(time.monotonic())[:2], (.1, .4))
        self.assertEqual(link.next_command(time.monotonic() + LEASE + 1), (0, 0, None))
        link.skill("roulade")
        self.assertEqual(link.next_command(time.monotonic() + LEASE + 1), (0, 0, None))
        link.skill("kick_left")
        link.stop_motion()
        self.assertEqual(link.next_command(time.monotonic()), (0, 0, None))

    def test_worker_stops_after_ui_heartbeat_stalls(self):
        class Recording:
            def __init__(self):
                self.moves = []
                self.moved = threading.Event()
                self.stopped = threading.Event()
            def call(self, method):
                return {}
            def move(self, vx=0, vyaw=0):
                self.moves.append((vx, vyaw))
                if vx:
                    self.moved.set()
                elif self.moved.is_set():
                    self.stopped.set()
        client = Recording()
        link = Link(client)
        link.start()
        link.connect_requested.set()
        try:
            self.assertEqual(link.events.get(timeout=1)[0], "connected")
            link.update(.1, .4)
            self.assertTrue(client.moved.wait(1))
            # No UI update: the independent worker must publish zero within the lease.
            self.assertTrue(client.stopped.wait(LEASE + .3))
        finally:
            link.done.set()
            link.join(timeout=1)
        self.assertEqual(client.moves[-1], (0, 0))


class WireTests(unittest.TestCase):
    def exchange(self, response, operation):
        with tempfile.TemporaryDirectory(prefix="duck-", dir="/tmp") as directory:
            path = str(Path(directory) / "robot.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen()
                server.settimeout(2)
                requests = []
                def serve():
                    with server.accept()[0] as peer:
                        with peer.makefile("rb") as stream:
                            requests.append(json.loads(stream.readline()))
                        peer.sendall((json.dumps(response) + "\n").encode())
                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    operation(RobotClient(path))
                finally:
                    thread.join(timeout=2)
                return requests[0]

    def test_move_contract(self):
        request = self.exchange({"id": 1, "result": {"accepted": True}}, lambda client: client.move(.12, -.4))
        self.assertEqual(request["method"], "robot.move")
        self.assertEqual(request["params"], {"vx": .12, "vy": 0, "vyaw": -.4})

    def test_reverse_preset_wire_contract(self):
        controls = Controls()
        controls.press("s")
        request = self.exchange({"id": 1, "result": {"accepted": True}},
                                lambda client: client.move(*controls.twist(*SPEED_PRESETS["仿真实测"])))
        self.assertEqual(request["params"], {"vx": -.4, "vy": 0, "vyaw": 0})

    def test_skill_refusal_is_not_success(self):
        with self.assertRaisesRegex(RpcError, "policy"):
            self.exchange({"id": 1, "result": {"accepted": False, "reason": "policy is disabled"}},
                          lambda client: client.call("robot.do", {"skill": "roulade"}))

    def test_rpc_error_is_reported(self):
        with self.assertRaisesRegex(RpcError, "unknown"):
            self.exchange({"id": 1, "error": {"code": -32601, "message": "unknown method"}},
                          lambda client: client.call("robot.health"))

    def test_disconnect_does_not_retry_a_skill(self):
        class Broken:
            def call(self, method):
                return {}
            def move(self, *args):
                raise OSError("connection lost")
        link = Link(Broken())
        link.connect_requested.set()
        link.start()
        try:
            self.assertEqual(link.events.get(timeout=1)[0], "connected")
            self.assertEqual(link.events.get(timeout=1)[0], "disconnected")
            self.assertEqual(link.next_command(time.monotonic()), (0, 0, None))
        finally:
            link.done.set()
            link.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
