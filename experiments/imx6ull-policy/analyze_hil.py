"""Summarize retained ground truth separately from control-loop timing."""
import argparse
import json
import math
from pathlib import Path


def analyze(root, label, duration):
    report = json.loads((root / f"hil-results-{label}.json").read_text())
    trace = json.loads((root / "out" / f"hil-trace-{label}.json").read_text())
    enabled = report["enabled_host_s"]
    sensors = trace["sensors"]
    steady = [s for s in sensors if s["host_s"] >= enabled+5]
    phases = {}
    for name, lo, hi in [("stand", 0, .25), ("forward", .25, .60),
                         ("turn", .60, .80), ("stop", .80, 1)]:
        samples = [s for s in sensors if enabled+lo*duration <= s["host_s"] < enabled+hi*duration]
        if not samples:
            continue
        first, last = samples[0], samples[-1]
        delta = [b-a for a, b in zip(first["trunk"], last["trunk"])]
        w, x, y, z = first["imu"]["quat"]
        yaw = math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
        states = [s for s in trace["state"] if enabled+lo*duration <= s["host_s"] < enabled+hi*duration]
        phases[name] = {"seconds": last["host_s"]-first["host_s"],
                        "displacement_m": delta,
                        "horizontal_net_m": math.hypot(*delta[:2]),
                        "forward_projected_m": delta[0]*math.cos(yaw)+delta[1]*math.sin(yaw),
                        "applied_abs_max": [max((abs(s["move"]["applied"][i]) for s in states), default=0) for i in range(3)]}
    result = {
        "label": label, "phases": phases,
        "steady_after_5s": {"min_trunk_z_m": min(s["trunk_z"] for s in steady),
                             "max_gravity_z": max(s["imu"]["gravity"][2] for s in steady)},
        "finite_targets": all(len(s["targets"]) == 15 and all(math.isfinite(v) for v in s["targets"]) for s in trace["state"]),
        "last_health": trace["health"][-1],
        "last_movement": trace["state"][-1]["move"],
        "safety_limits": sorted({limit for s in trace["state"] for limit in s["move"].get("limited_by", [])}),
    }
    drop = report.get("drop_after_s")
    if drop is not None:
        after = [s for s in trace["state"] if s["host_s"] > enabled+drop]
        gated = [s for s in after if "deadman" in s["move"].get("limited_by", [])]
        settled = [s for s in after if max(map(abs, s["move"]["applied"])) < 1e-6]
        stopped = [s for s in after if s["host_s"] > enabled+drop+2]
        result["deadman"] = {
            "first_reported_gate_s_after_cutoff": gated[0]["host_s"]-enabled-drop if gated else None,
            "first_below_1e_6_s_after_cutoff": settled[0]["host_s"]-enabled-drop if settled else None,
            "samples_after_2s": len(stopped),
            "all_below_1e_6_after_2s": bool(stopped) and all(max(map(abs, s["move"]["applied"])) < 1e-6 for s in stopped),
            "note": "command EMA remains after the deadman gate; this is a smooth stop, not instantaneous zero or emergency stop",
        }
    (root / f"hil-analysis-{label}.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--duration", type=float, default=60)
    args = parser.parse_args()
    analyze(Path(__file__).resolve().parent, args.label, args.duration)
