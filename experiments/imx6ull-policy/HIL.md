# i.MX6ULL + existing MuJoCo body — 2026-09-19

The **control chain runs on the actual board**; the existing `microduck_rl`
simulator supplies the body. The 60-second run mostly sustained 50 Hz, but had
two ticks longer than 20 ms. **Locomotion acceptance failed:** forward commands
reached the controller but did not produce sustained forward walking. The Mac
ONNX Runtime baseline reproduced the symptom, so it is not specific to the ARM
executor. The underlying model/controller/simulator mismatch is not yet isolated.

## Boundary

```text
Mac: original MuJoCo body_server (physics, joints, IMU)
  → existing sensor JSON → USB CDC tunnel → board RemoteIo
  → real robotd: observations → FP32 policy → filters/safety → joint targets
  → USB CDC tunnel → original body_server actuators → physics → sensors …

Mac test client → same USB tunnel → board robotd JSON-RPC
```

`robotd` runs the existing enable/homing, command deadman, observation builder,
policy selection, action feedback, scaling/filtering, safety, odometry,
kinematics, slow-sensor reads and state publication. Only inference has an
opt-in specialized backend. The control mechanism belongs to
[robotd-design.md](../../docs/design/robotd-design.md); the unmodified sensor and
actuator protocol belongs to [simulation.md](../../docs/design/simulation.md).

The Mac is **not** generating policy actions. `hil-proxy.c` only transports the
unchanged body protocol and JSON-RPC, prefixed with separate channel letters.
The board proxy binds localhost:7819 and connects the daemon's Unix socket;
the Mac harness forwards to an isolated simulator on localhost:7811. No
simulator source or physical bus driver was changed.

Physical servos/IMU, UART turnaround, camera, microphone, ToF and other daemons
are outside this test. Simulator voltage, motor temperature and current are
synthetic; they are not measurements of attached hardware. Audio and chorale
are disabled. Only the exported velstand policy is loaded; optional skills and
sitstand are disabled. This is not a full production software image or release.

## Measurements

Board: single Cortex-A7, maximum 792 MHz, original ondemand governor, Debian 10.
Same FP32 model and export as [the inference results](RESULTS.md).

| Measurement | Board default60 | Board deadman20 |
|---|---:|---:|
| Enabled duration | 60 s | 20 s |
| Driving ticks recorded (excludes homing) | 2,899 | 900 |
| Whole tick mean | 11.220 ms | 11.129 ms |
| Whole tick P99 | 15.361 ms | 15.064 ms |
| Whole tick max | 21.893 ms | 22.861 ms |
| Driving ticks >20 ms | 2 | 1 |
| Last sampled achieved rate | 50.13 Hz | 49.83 Hz |
| Read errors / stale IMU reported | 0 / 0 | 0 / 0 |
| Mac-observed read-request to write-ack mean | 8.039 ms | 7.579 ms |

Whole-tick times are measured **on the board**, from immediately after scheduler
wake through sensor read, all control work, target write, publication and any
slow-sensor reads. They exclude interval sleep, so are work-duration overruns,
not a complete scheduled-release deadline analysis. The daemon's own `missed`
counter is checked before slow reads; the separate recorder includes them.
Do not equate the narrower Mac transport interval with whole-loop duration or
add these two measurements together.

The recorder preallocates 30,000 rows, does no per-tick file I/O, and writes on
clean shutdown. It stops recording at capacity (~10 minutes). The 50 Hz state
subscription can skip publications due to its rate cap (~31 Hz received); it
does not mean the control loop ran at that lower rate. Simulation elapsed time
divided by wall time was 1.00026 in default60.

The conclusion is **50 Hz is feasible in this configuration, but not guaranteed
with zero overruns**. Inference-only headroom does not equal complete-loop
headroom. These short tests are not long-duration or loaded-system certification.

## Behavior, including failures

The ordinary schedule was 15 s standing, 21 s at +0.2 m/s, 12 s at +0.3 rad/s,
then 12 s stopped. Commands traversed the real IPC, and `robot.state` confirmed
the requested/applied values. All published target vectors had 15 finite values.

- Startup: without sitstand, enable triggers the existing two-second homing.
  The body tips forward, reaching trunk height ~0.0435 m, and then the policy
  restores an upright stance. This startup sequence is not accepted as safe.
- After the first five enabled seconds, minimum trunk height was 0.1148 m and
  projected gravity z stayed below -0.9997: it remained upright in this run.
- Forward: 21 s at +0.2 m/s produced only **0.0106 m net horizontal displacement**,
  or **0.00217 m projected onto the initial forward axis**. This is not successful
  velocity tracking. Startup displacement must not be counted as walking.
- The 40-second Mac baseline uses original ONNX Runtime and the same model,
  configuration, bridge and body: it also tipped/recovered, and its 14-second
  forward phase produced only 0.00815 m horizontal / 0.00387 m forward displacement.
  This rules out an ARM-only explanation of the symptom, not every porting bug.
- In deadman20, commands ceased at enabled second 14 while commanding yaw.
  State reported the deadman about 0.52 s later. The existing command EMA then
  decayed the applied command toward zero. This is a **smooth command stop**, not
  instantaneous motor shutdown or an emergency-stop test. Exact settling values
  are in `hil-analysis-deadman20.json`.

Further work should first isolate the existing policy/controller/simulator
behavior against the training inference setup, including startup pose, action
scale/filters and actuator/contact model. None was silently tuned to make these
results look better. Real servo integration should follow successful simulated
locomotion and a separate timing/safety check on the physical bus.

## Build and repeat

Requires the sibling `microduck_rl` checkout with its existing MuJoCo-capable
`.venv`, Rust with rustup, Zig 0.16, and `cargo-zigbuild`. First produce the
model/weights using [README.md](README.md). From the repository root:

```sh
rustup target add armv7-unknown-linux-musleabihf
cargo zigbuild --release --target armv7-unknown-linux-musleabihf \
  -p robotd --features imx6ull-mlp
cp target/armv7-unknown-linux-musleabihf/release/robotd \
  experiments/imx6ull-policy/out/robotd-hil
zig cc -target arm-linux-musleabihf -mcpu=cortex_a7 -O2 \
  -Wall -Wextra -Werror -static experiments/imx6ull-policy/hil-proxy.c \
  -o experiments/imx6ull-policy/out/hil-proxy
python3 experiments/imx6ull-policy/run_hil.py --duration 60 --label repeat60
python3 experiments/imx6ull-policy/analyze_hil.py repeat60 --duration 60
python3 experiments/imx6ull-policy/run_hil.py \
  --duration 20 --label repeat-deadman --drop-after 14
python3 experiments/imx6ull-policy/analyze_hil.py repeat-deadman --duration 20
```

The tested toolchain was isolated Rust 1.98.0 + ARM musl target. The binary is
static; no Python, compiler or ONNX Runtime is required on the board. The native
backend is enabled only by `imx6ull-mlp` and only for `.duckmlp` files. Ordinary
ONNX models still use the original runtime. The specialized format accepts only
this checked 61→512→256→128→14 topology, not arbitrary ONNX or LSTM files. It is
an experimental local export, not the signed production model delivery contract.

For the Mac control (set `ORT_DYLIB_PATH` to the local ONNX Runtime dylib):

```sh
cargo build -p robotd
cc -D_DARWIN_C_SOURCE -O2 -Wall -Wextra -Werror \
  experiments/imx6ull-policy/hil-proxy.c \
  -o experiments/imx6ull-policy/out/hil-proxy-host
python3 experiments/imx6ull-policy/run_hil.py --host --duration 40 --label host-repeat
python3 experiments/imx6ull-policy/analyze_hil.py host-repeat --duration 40
```

The scripts prompt for the existing board password or read
`MICRODUCK_BOARD_PASSWORD`. Close other serial terminals. They upload with
SHA-256 validation into `/home/debian/microduck-policy-bench`, launch their own
daemon and simulator, then stop both and restore the console. Existing user
simulator services are left alone. No boot service, CPU governor or production
installation is modified; binaries/config/results remain for reuse.

## Evidence and checks

- `hil-results-default60.json`, `hil-results-deadman20.json`: board timing,
  health samples, protocol operation counts and daemon logs.
- `hil-results-host-ort40.json`: original Mac ONNX baseline.
- `hil-analysis-*.json`: phase displacements, steady-state posture and command
  stop analysis, generated by `analyze_hil.py`.
- Ignored `out/hil-trace-*.json`: raw simulated sensors and robot states;
  `out/hil-timings-*.json`: on-board per-tick timings.
- `hil-results-pilot.json` is an earlier transport bring-up only: its shutdown
  bridge exited too early, creating a spurious long tick. The graceful-shutdown
  fix is included in both reported board runs above; the pilot is not a benchmark.

Verification: `duck-control` feature-on tests 72 passed; `robotd` feature-on tests
151 passed, one ignored; `duck-control` default-feature tests 70 passed; Rust
format check passed; proxy compiled warning-clean for ARM and macOS. The earlier
5,120-case numerical comparison against ONNX Runtime also remains applicable.
