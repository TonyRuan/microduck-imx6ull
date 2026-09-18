# Control-thread stage profile — 2026-09-19

**Historical scalar-build baseline:** a subsequent compiler audit found that this
robotd build did not enable the existing NEON kernel; see the correction and new
A/B results in [NEON-BUILD.md](NEON-BUILD.md). The measurements below remain valid
for the recorded binary, not for the corrected NEON deployment.

Implemented optional stage timing and exercised it on the actual i.MX6ULL with
an owned headless Mac MuJoCo body. This change measures the existing controller;
it does not optimize inference, change weights, alter safety, or change the
governor. Timing semantics and stage boundaries belong to
[robotd-design.md §1.4](../../docs/design/robotd-design.md#optional-hil-stage-timing).

## Conditions and evidence

[stage-results.json](stage-results.json) contains compact distributions, per-policy
breakdowns, executable/model-index hashes, board identity and sampled frequencies.
All three runs used the same new ARM executable, the alpha profile (seven models
resident), approximately 29 seconds of commanded actions after connection, and
roughly 20 Hz command refresh. Actions were idle, forward walk, stop, sit, rise,
left/right kick, ground pick, roll, recovery. This is a performance test, not
a new physical-action or locomotion-quality acceptance test.

The governor remained `ondemand`; frequency was sampled at 792 MHz before and
after each run, **not locked or continuously sampled**. Samples exclude the first
five seconds and select driving ticks. All eight policy labels were observed in
both instrumented runs; there were no steady driving `held` samples. Both stage
traces had zero dropped rows and zero missing CPU samples.

## 20 Hz state subscription: per-tick stages

1,386 steady driving ticks, from 1,637 total recorded ticks. Times in milliseconds.

| Stage | Mean wall | Wall P99 | Mean thread CPU |
|---|---:|---:|---:|
| Sensor read | 2.336 | 3.407 | 0.672 |
| State estimation / odometry | 0.078 | 0.094 | 0.070 |
| Control preparation / gating | 0.053 | 0.061 | 0.052 |
| Policy selection | 0.016 | 0.021 | 0.015 |
| Observation construction | 0.014 | 0.018 | 0.014 |
| Inference | 5.289 | 8.454 | 5.178 |
| Control postprocessing | 0.041 | 0.159 | 0.030 |
| Auxiliary motion | 0.022 | 0.143 | 0.016 |
| Actuator write / safety apply | 1.996 | 3.265 | 0.610 |
| State publication / queueing | 0.930 | 2.439 | 0.189 |
| Maintenance / slow sensors | 0.071 | 2.191 | 0.042 |
| **Whole measured tick** | **10.846** | **15.686** | **6.889** |

Whole-tick P95 was 13.757 ms and maximum 17.765 ms. Mean wake lateness was
0.965 ms; the maximum same-frame wake-plus-work was 18.658 ms. This particular
steady sample had zero work-duration and zero scheduled-finish overruns of 20 ms.
Stage percentiles **must not be added** to reconstruct the whole-tick percentile.

Inference accounts for about 75.2% of the measured control-thread CPU time and
48.8% of wall work time. Sensor and actuator round trips together consume
4.332 ms wall but only 1.282 ms control-thread CPU. At nominal 50 Hz, measured
6.889 ms/tick corresponds to about 34.4% of one CPU for these control-thread
intervals; this excludes IPC workers, proxy, OS and profiling buffer insertion.
It is not the whole-system CPU usage or a replacement for [RESOURCE-USAGE.md](RESOURCE-USAGE.md).

Per-policy mean inference wall times ranged from 5.157 ms (left kick, 25 samples)
to 5.344 ms (right kick, 25 samples). These are short mixed-workload samples,
not a ranking of actor complexity. They must not replace the standalone actor
benchmarks or be compared as though all runs had fixed identical CPU frequencies,
caches and concurrent work.

## Controls and uncertainty

The legacy whole-tick recorder was enabled in all runs, allowing an equivalent
boundary comparison when stage probes were disabled:

| Run | Steady ticks | Legacy mean | Legacy P99 | Legacy max | Work >20 ms |
|---|---:|---:|---:|---:|---:|
| Stage probes off, subscription 20 Hz | 1,377 | 10.871 | 17.056 | 38.452 | 7 |
| Stage probes on, subscription 20 Hz | 1,386 | 10.855 | 15.699 | 17.787 | 0 |
| Stage probes on, no state subscriber | 1,379 | 10.149 | 14.045 | 19.776 | 0 |

The profiled and baseline averages differed by only about 0.015 ms in these
sequential runs. **This does not measure zero or negative instrumentation cost.**
Independent short runs under dynamic frequency and host scheduling cannot resolve
small probe overhead; repeated interleaved runs or a clock microbenchmark would
be needed to quantify it. The baseline's seven overruns demonstrate why the zero
overruns in the next run do not establish a hard-real-time guarantee.

Without a state subscriber, stage-measured work averaged 10.140 ms (P99 14.037),
and publication fell from 0.930 to 0.027 ms wall, 0.189 to 0.019 ms CPU. That
removes frame construction and subscriber wakeups, but the publication stage
does **not** measure serialization on the IPC worker. Other stages and wake
latency also varied: this run had one scheduled-finish overrun (21.384 ms)
despite zero work-duration overruns. Do not equate shorter work with guaranteed
deadline improvement or attribute the entire mean difference to telemetry.

## What to optimize next

The primary measured control-thread CPU target is inference. Observation building
and policy switching are small; optimizing them first would have limited impact.
For this HIL setup, transport round trips and state telemetry also deserve attention
when reducing wall latency. Their simulator/USB costs cannot be used as estimates
of the eventual physical sensor/servo bus. Measure that bus separately.

No kernel, inference, protocol or scheduling optimization was made in this change.
The existing roller stability and low-command walking issues are unchanged.

## Reproduce

Use the existing [HIL ARM build instructions](HIL.md#build-and-repeat), including
`--features imx6ull-mlp`, and copy the resulting binary to `out/robotd-hil`.
Models, body environment and saved credential prerequisites are the same as
the keyboard panel. Close the panel or switch it to Mac local before running;
the harness does not terminate another session or use physical motor I/O.
From the repository root, with the panel Python environment installed:

```bash
.keyboard-venv/bin/python experiments/imx6ull-policy/profile_stages.py \
  --baseline --output experiments/imx6ull-policy/out/stage-baseline-new
.keyboard-venv/bin/python experiments/imx6ull-policy/profile_stages.py \
  --output experiments/imx6ull-policy/out/stage-profile-new
.keyboard-venv/bin/python experiments/imx6ull-policy/profile_stages.py \
  --subscribe-hz 0 --output experiments/imx6ull-policy/out/stage-no-telemetry-new
```

Choose new output directory names for each run; existing directories are refused.
The harness enables timing only in its own board login shell, retrieves JSON after
clean shutdown, then logs out. No persistent service/environment is modified.
`--profile velstand` and `--profile roller` select the existing alternative
profiles; roller remains experimental. The script reads the existing macOS
Keychain credential, without printing or saving it again.

Each output contains `loop.json`, `metadata.json`, `summary.json`, and (unless
baseline) `stages.json`. Raw captures for this record remain in ignored `out/`:
`profile-baseline-20260919-b`, `profile-stages-20260919`, and
`profile-no-telemetry-20260919`. Compact results are stored alongside the sources;
raw files are not. Remote raw files have unique `profile-<id>` prefixes under
the existing HIL experiment directory, recorded in each local metadata file.

Verification: robotd default tests 151 passed / 1 ignored; with the feature,
153 passed / 1 ignored; duck-control feature tests 72 passed (8 additional ORT
integration tests ignored); stage-summary tests 4 passed; keyboard regression
suite 29 passed / 1 GUI-display test skipped. ARM release cross-build succeeded.
