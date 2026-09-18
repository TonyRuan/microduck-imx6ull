"""Deploy the offline benchmark and record burst, paced, and CPU-contention runs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from serial_shell import SerialShell, password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    shell = SerialShell(password())
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "runs": [],
              "scope": "specialized FP32 actor inference; no motor/sensor I/O; synthetic observations"}
    board = "/home/debian/microduck-policy-bench"
    try:
        report["environment"] = shell.command(
            "uname -a; cat /proc/cpuinfo; free -m; "
            "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; "
            "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies; "
            "cat /sys/class/thermal/thermal_zone0/temp; uptime"
        )
        shell.command(f"mkdir -p {board}")
        report["uploaded_sha256"] = {}
        for name in ["policy-bench-armv7", "weights.bin", "fixtures.bin", "model.json", "velstand.onnx"]:
            report["uploaded_sha256"][name] = shell.upload(args.out / name, board + "/" + name)
        shell.command(f"chmod +x {board}/policy-bench-armv7")
        report["model"] = json.loads((args.out / "model.json").read_text())
        tests = [
            ("burst_5000", "./policy-bench-armv7 weights.bin fixtures.bin 5000 0"),
            ("paced_50hz_60s", "./policy-bench-armv7 weights.bin fixtures.bin 3000 20"),
            ("paced_50hz_60s_cpu_contention",
             "timeout 100 sha256sum /dev/zero >/dev/null & load_pid=$!; "
             "./policy-bench-armv7 weights.bin fixtures.bin 3000 20; bench_rc=$?; "
             'kill "$load_pid" 2>/dev/null; wait "$load_pid" 2>/dev/null; '
             'printf "BENCH_EXIT=%s\\n" "$bench_rc"'),
        ]
        for label, command in tests:
            print("START", label, flush=True)
            output = shell.command(f"cd {board}; {command}", timeout=100)
            records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
            if len(records) != 2 or records[0]["failed_values"]:
                raise RuntimeError(output)
            report["runs"].append({"name": label, "validation": records[0], "performance": records[1]})
            args.results.write_text(json.dumps(report, indent=2) + "\n")
            print(label, json.dumps(records), flush=True)
        report["after"] = shell.command(
            "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; "
            "cat /sys/class/thermal/thermal_zone0/temp; uptime; pgrep -a sha256sum || true"
        )
        args.results.write_text(json.dumps(report, indent=2) + "\n")
    finally:
        shell.close()


if __name__ == "__main__":
    main()
