# All-model ARM port and action checks — 2026-09-19

All ten actors in the local pinned v5 policy set were exported, deployed and
numerically verified on the actual i.MX6ULL. The walking profiles passed the
closed-loop action-transition and end-posture checks below. **Roller inference
works, but roller stability has not passed acceptance on either backend.**

Panel operation belongs to [Keyboard control](../../docs/robot/simulation.md#keyboard-control).
Profile selection, scene settings and session ownership belong to the
[simulation design](../../docs/design/simulation.md#2-where-the-seam-is).
No physical motor or sensor driver was exercised, no system service was installed,
and no model was retrained or quantized.

## What was ported

| Actor | Purpose |
|---|---|
| velstand | Default walking / idle |
| alpha_walking | Alternate walking |
| alpha_stand | Independent standing |
| alpha_sitstand | Sitting and rising |
| alpha_ground_pick | Ground-pick motion |
| ball_kick_left / ball_kick_right | Left / right kick |
| roulade | Forward roll |
| roller / roller_crouch | Wheeled motion / crouching (experimental) |

Every graph passed the existing exporter's topology/type/attribute checks:
61→512→256→128→14, normalization, four dense layers and three ELUs.
The existing native executor and multi-policy controller therefore needed no
new neural-network operators or generic ONNX runtime. Mac uses the corresponding
ONNX files; the board uses the exported `.duckmlp` weights.

## Numerical and timing results

[all-policy-results.json](all-policy-results.json) contains model/weight hashes,
reference-library versions, validation counts and per-model board timings.
Each actor passed 5,120 observations / 71,680 output values: **51,200 observations
and 716,800 values total**, zero tolerance failures. NaN and Inf inputs were
rejected for all ten actors. The reference for this follow-up is ONNX Runtime
1.24.4 (not the 1.30.0 used for the original single-model report).

Tolerance is `abs(error) <= 1e-4 + 1e-4*abs(reference)`, not a fixed absolute bound
and not bitwise equality. The largest absolute difference across all fixtures was
0.0009765625; it passed that combined absolute/relative tolerance.

Each short timing run used 250 scheduled iterations at 50 Hz, after validation
and warm-up. Mean inference latency was **3.60–3.79 ms**, maximum **5.70–6.34 ms**
across the individual runs, with zero recorded scheduled deadline misses in those
five-second actor-only benchmarks. These numbers exclude the USB/body/control
chain and do not prove long-duration or loaded real-time guarantees.

## Closed-loop results

[action-results.json](action-results.json) records compact telemetry summaries
from the actual `robotd` processes, not mocked GUI acknowledgements. Tests used
independent headless MuJoCo bodies, 20 Hz state subscriptions, 20 Hz command
refresh, and the same model profiles on Mac and board. Mac and board comparisons
ran concurrently on separate bodies; they are not bit-identical physics traces.

| Profile | Board sampled loop Hz | Final cumulative missed ticks | Action transitions | Posture at stage ends |
|---|---:|---:|---|---|
| velstand | 49.73–50.11 | 5 | All expected labels observed | Upright |
| alpha + independent stand | 47.95–50.25 | 14 | All expected labels observed | Upright |
| roller (final scene settings) | 49.85–50.17 | 3 | Available labels observed | **Fallen — failed** |

Mac sampled ranges were 49.93–50.23, 49.94–50.25 and 49.93–50.22 Hz respectively,
with zero cumulative misses in these short runs. A missed counter is not a
whole-tick latency percentile; see the original [HIL timing definition](HIL.md#measurements).

The two walking profiles exercised idle, forward motion, stop, sit, rise, both
kicks, ground-pick, roll and recovery. The alpha profile actually switched
`stand → walk → stand`; sit/rise changed the sitting latch both ways. The board's
roll reached approximately 175.6° trunk tilt under velstand and 166.1° under alpha,
then returned to under 1° tilt in both cases. No inference fallback to `held` was
observed. This demonstrates model execution, motion and recovery, **not** a ball
being kicked or an object being picked up: neither object was present in the scene.

Roller and crouch load and execute on both CPUs, but the body falls before stable
control is established. Matching the reference launcher's 0.1385 m initial
height and 0.003 passive-wheel friction did not resolve it. A dedicated Mac startup
trace observed the first fall at 1.101 s, still in `homing` (86.7° tilt); the roller
actor first took over at 2.060 s with 87.9° tilt. This identifies a startup/homing
problem before actor balancing, not an ARM numerical-port failure. Stable roller
startup/recovery remains unresolved. No homing bypass or artificial stabilization
was used to hide this failure. The panel labels roller experimental and, like the
RL reference launcher, excludes foot-trained kicks and roll from this profile.

The existing low-command walking initiation issue remains; this change adds
models and action paths, not retraining or gait-quality fixes.

## Reproduce

Build the existing Mac/ARM binaries as described in [HIL.md](HIL.md#build-and-repeat),
and the standalone benchmark as described in [README.md](README.md#reproduce-on-the-mac).
The Mac RL environment needs ONNX, ONNX Runtime and NumPy; versions used here are
recorded per model in `all-policy-results.json`. A fresh Git clone contains neither
the generated binaries nor the model bundle. If the default policy cache is missing,
fetch the pinned set with `sh scripts/seed-policies.sh "$HOME/.cache/duck-sim/policies"`,
or pass `--models /absolute/path/to/the/v5/set` to the exporter.
Then, from the repository root:

```sh
../microduck_rl/.venv/bin/python experiments/imx6ull-policy/prepare_policies.py
.keyboard-venv/bin/python experiments/imx6ull-policy/verify_policies.py --keychain
.keyboard-venv/bin/python experiments/imx6ull-policy/verify_actions.py \
  --backend board --results experiments/imx6ull-policy/out/actions-board.json
.keyboard-venv/bin/python experiments/imx6ull-policy/verify_actions.py \
  --backend mac --results experiments/imx6ull-policy/out/actions-mac.json
DUCK_GUI_TEST=1 .keyboard-venv/bin/python -m unittest discover -s scripts -p 'test_duck_*.py'
```

Close/switch away from any existing board panel first. `--keychain` requires a
saved credential; omit it for an interactive password prompt in the numerical
benchmark. Action tests use the same Keychain-backed managed connection as the
panel. `--profile alpha` (or `velstand` / `roller`) limits an action run.
Raw traces remain in ignored `out/`; `verify_actions.summarize` derives the compact
metrics recorded here. A successful action-script exit checks transitions and
absence of inference holds; it is **not** a physical-stability pass. Inspect the
posture metrics as well. The complete Python/Tk regression suite passed 30 tests,
including full/mini layout, profile coverage, checksum rejection and slot fallback
rejection. Restart an older open panel to load the new controls.

The subsequent full-controller resource measurement is recorded separately in
[RESOURCE-USAGE.md](RESOURCE-USAGE.md); its CPU percentages and RAM footprint
must not be confused with the actor-only timings above.
