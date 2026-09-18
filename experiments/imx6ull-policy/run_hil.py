"""Run the real board robotd against the existing microduck_rl MuJoCo body server."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time

from serial_shell import SerialShell, password


class HostTransport:
    """Identical bridge, but a local daemon for ONNX Runtime baseline comparisons."""
    def __init__(self, root, model):
        self.pending = b""
        out = root / "out"
        config = (root / "robotd-hil.toml").read_text().replace(
            "/home/debian/microduck-policy-bench/velstand.duckmlp", str(model))
        (out / "host-hil.toml").write_text(config)
        env = os.environ.copy()
        env["DUCK_HIL_TIMINGS"] = str(out / "host-timings.json")
        self.process = subprocess.Popen([
            str(out / "hil-proxy-host"), str(root.parents[1] / "target/debug/robotd"),
            str(out / "host-hil.toml"), "/tmp/microduck-hil-host.sock",
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=out, env=env)
        self.fd = self.process.stdout.fileno()

    def send(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def until(self, marker, timeout=15):
        deadline = time.monotonic()+timeout
        while marker not in self.pending:
            if time.monotonic()>deadline:
                raise TimeoutError(marker)
            if select.select([self.fd], [], [], .1)[0]:
                data = os.read(self.fd, 65536)
                if not data:
                    raise EOFError("local proxy exited")
                self.pending += data
        before, self.pending = self.pending.split(marker, 1)
        return before

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def stats(values):
    values = sorted(values)
    if not values:
        return {}
    return {"count": len(values), "mean": sum(values)/len(values),
            "p50": values[int((len(values)-1)*.5)], "p99": values[int((len(values)-1)*.99)],
            "max": values[-1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--label", default="default")
    parser.add_argument("--vx", type=float, default=.2)
    parser.add_argument("--host", action="store_true", help="local ONNX Runtime baseline, no board")
    parser.add_argument("--drop-after", type=float, help="stop sending move commands after this many enabled seconds")
    parser.add_argument("--rl", type=Path, default=Path(__file__).resolve().parents[3] / "microduck_rl")
    parser.add_argument("--port", type=int, default=7811)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    out = root / "out"
    board = "/home/debian/microduck-policy-bench"
    if args.duration <= 5 or not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("duration must exceed 5 seconds and label must be alphanumeric, dash or underscore")
    report = {"label": args.label, "duration_s": args.duration, "vx": args.vx,
              "drop_after_s": args.drop_after,
              "scope": "real ARM robotd with native FP32 policy; USB body/IPC tunnel to existing MuJoCo body_server"}
    trace = {"sensors": [], "state": [], "health": [], "rpc": []}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(args.rl / "src")
    body_log = (out / f"body-{args.label}.log").open("w")
    body_process = subprocess.Popen([
        str(args.rl / ".venv/bin/python"), "-m", "mjlab_microduck.sim.body_server",
        "--headless", "--port", str(args.port), "--keyframe", "HOME",
    ], env=env, stdout=body_log, stderr=subprocess.STDOUT)
    shell = None
    sim = None
    tunneled = False
    proxy_stopped = False
    try:
        until = time.monotonic() + 15
        while True:
            try:
                sim = socket.create_connection(("127.0.0.1", args.port), timeout=1)
                sim.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                break
            except OSError:
                if body_process.poll() is not None or time.monotonic() > until:
                    raise RuntimeError((out / f"body-{args.label}.log").read_text())
                time.sleep(.1)
        if args.host:
            shell = HostTransport(root, out / "velstand.onnx")
            report["scope"] = "local Mac robotd with original ONNX Runtime; same body and IPC bridge"
        else:
            shell = SerialShell(password())
            shell.command(f"mkdir -p {board}")
            for src, dest in [
            (out / "robotd-hil", "robotd-hil"),
            (out / "hil-proxy", "hil-proxy"),
            (out / "weights.bin", "velstand.duckmlp"),
            (root / "robotd-hil.toml", "robotd-hil.toml"),
            ]:
                shell.upload(src, board + "/" + dest)
            shell.command(f"chmod +x {board}/robotd-hil {board}/hil-proxy")
            shell.send((f"cd {board}; hil_tty=$(stty -g); stty raw -echo; "
                    f"./hil-proxy ./robotd-hil robotd-hil.toml {board}/robotd-hil.sock; "
                        "stty \"$hil_tty\"; printf '\\nHIL_DONE\\n'\r").encode())
        tunneled = True
        shell.until(b"R\tready\n")
        serial_buffer = shell.pending
        shell.pending = b""
        sim_buffer = b""
        operations = Counter()
        pending_op = None
        read_started = None
        read_write_ms = []
        start = time.monotonic()
        next_health = start + .5
        next_move = start
        enabled_at = None
        enable_sent = False
        stopping_at = None
        reqid = 10
        methods = {}

        def rpc(method, params=None, notification=False):
            nonlocal reqid
            message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            if not notification:
                message["id"] = reqid
                methods[reqid] = method
                reqid += 1
            shell.send(b"I\t" + json.dumps(message, separators=(",", ":")).encode() + b"\n")

        print("HIL started", args.label, flush=True)
        while True:
            now = time.monotonic()
            elapsed = now-start
            if enabled_at is not None and now-enabled_at > args.duration:
                if stopping_at is None:
                    stopping_at = now
                    shell.send(b"S\n")
                elif now-stopping_at>10:
                    raise TimeoutError("daemon did not stop cleanly")
            if enabled_at is None and elapsed > 20:
                raise RuntimeError("robotd did not become enabled: " + repr(trace["rpc"][-5:]))
            if now >= next_health and stopping_at is None:
                rpc("robot.health")
                next_health = now+1
            if enabled_at is not None and now >= next_move and stopping_at is None:
                t = now-enabled_at
                # Stand, walk forward, turn, then stop. All commands traverse the real IPC.
                phase = t / args.duration
                vx = args.vx if .25 <= phase < .60 else 0
                vyaw = .30 if .60 <= phase < .80 else 0
                if args.drop_after is None or t < args.drop_after:
                    rpc("robot.move", {"vx": vx, "vy": 0, "vyaw": vyaw}, notification=True)
                next_move = now+.05
            ready, _, _ = select.select([shell.fd, sim], [], [], .005)
            if shell.fd in ready:
                data = os.read(shell.fd, 65536)
                if not data:
                    raise EOFError("USB disconnected")
                serial_buffer += data
            while b"\n" in serial_buffer:
                line, serial_buffer = serial_buffer.split(b"\n", 1)
                if not line:
                    continue
                channel, payload = line[:1], line[2:]
                if channel == b"B":
                    request = json.loads(payload)
                    pending_op = request["op"]
                    operations[pending_op] += 1
                    if pending_op == "read":
                        read_started = time.monotonic()
                    sim.sendall(payload+b"\n")
                elif channel == b"I":
                    response = json.loads(payload)
                    t = time.monotonic()-start
                    if response.get("method") == "robot.state":
                        trace["state"].append({"host_s": t, **response["params"]})
                    elif "result" in response:
                        result = response["result"]
                        method = methods.pop(response.get("id"), None)
                        trace["rpc"].append({"host_s": t, **response})
                        if "healthy" in result:
                            trace["health"].append({"host_s": t, **result})
                            if result["healthy"] and not enable_sent:
                                rpc("robot.subscribe", {"hz": 50})
                                rpc("robot.enable", {"on": True})
                                enable_sent = True
                        if method == "robot.enable" and result.get("accepted"):
                            if enabled_at is None:
                                enabled_at = time.monotonic()
                                print("enabled", result, flush=True)
                    else:
                        trace["rpc"].append({"host_s": t, **response})
                elif channel == b"E":
                    trace["rpc"].append({"host_s": elapsed, "transport_error": payload.decode(errors="replace")})
                elif channel == b"R":
                    if payload == b"stopped" and stopping_at is not None:
                        proxy_stopped = True
                        break
                    raise RuntimeError("board proxy stopped: " + payload.decode())
                else:
                    raise RuntimeError("unexpected serial frame: " + repr(line[:200]))
            if proxy_stopped:
                shell.pending = serial_buffer
                break
            if sim in ready:
                data = sim.recv(65536)
                if not data:
                    raise EOFError("simulator disconnected")
                sim_buffer += data
            while b"\n" in sim_buffer:
                line, sim_buffer = sim_buffer.split(b"\n", 1)
                answer = json.loads(line)
                if pending_op == "read" and "positions" in answer:
                    trace["sensors"].append({"host_s": time.monotonic()-start, **answer})
                if pending_op == "write" and read_started is not None:
                    read_write_ms.append((time.monotonic()-read_started)*1000)
                    read_started = None
                shell.send(b"B\t"+line+b"\n")
                pending_op = None
        report["operations"] = dict(operations)
        report["read_request_to_write_ack_on_mac_ms"] = stats(read_write_ms)
        report["enabled_host_s"] = enabled_at-start if enabled_at else None
        report["wall_s"] = time.monotonic()-start
    finally:
        if shell is not None:
            if tunneled:
                if not proxy_stopped:
                    shell.send(b"Q\n")
                # Proxy reaps its own child, then restores the shell's terminal mode.
                if not args.host:
                    shell.until(b"HIL_DONE\r\n", timeout=15)
                    shell.until(b"__DUCK_PROMPT__ ")
                tunneled = False
                if args.host:
                    report["robotd_log"] = (out / "robotd-hil.log").read_text()
                    timing_text = (out / "host-timings.json").read_text() if (out / "host-timings.json").exists() else "{}"
                else:
                    report["robotd_log"] = shell.command(f"tail -100 {board}/robotd-hil.log")
                    timing_text = shell.command(f"cat {board}/hil-timings.json", timeout=30)
                try:
                    timings = json.loads(timing_text)
                    report["board_tick_ms"] = stats([r[1] for r in timings["rows"] if r[2]])
                    report["board_driving_over_20_ms"] = sum(r[1]>20 for r in timings["rows"] if r[2])
                    (out / f"hil-timings-{args.label}.json").write_text(json.dumps(timings))
                except (ValueError, KeyError):
                    if not args.host:
                        report["timing_error"] = timing_text
            shell.close()
        if sim is not None:
            sim.close()
        body_process.terminate()
        try:
            body_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            body_process.kill();body_process.wait()
        body_log.close()
        (out / f"hil-trace-{args.label}.json").write_text(json.dumps(trace))
        report["health"] = trace["health"]
        report["policy_counts"] = dict(Counter(s["policy"] for s in trace["state"]))
        if trace["sensors"]:
            sensors = trace["sensors"]
            report["body"] = {
                "first_trunk": sensors[0]["trunk"], "last_trunk": sensors[-1]["trunk"],
                "min_trunk_z": min(s["trunk_z"] for s in sensors),
                "max_gravity_z": max(s["imu"]["gravity"][2] for s in sensors),
                "sim_seconds_per_wall_second": (sensors[-1]["sim_time"]-sensors[0]["sim_time"])/(sensors[-1]["host_s"]-sensors[0]["host_s"]),
            }
        (root / f"hil-results-{args.label}.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items() if k not in ("health","robotd_log")},indent=2),flush=True)


if __name__ == "__main__":
    main()
