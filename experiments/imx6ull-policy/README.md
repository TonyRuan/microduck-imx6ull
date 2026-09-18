# i.MX6ULL gait inference and hardware-in-the-loop

This experiment initially ran `velstand.onnx` and now supports all ten actors from
the repository's pinned `pollen-robotics/microduck-policies` v5 set on an Embedfire
i.MX6ULL. It specializes their shared graph to FP32 C with ARM NEON:
normalization, four dense layers, and three
ELU activations. Weights are unchanged; there is no quantization or retraining.

**This is not an ONNX Runtime port.** The original standalone benchmark below
establishes numerical agreement and inference performance only. The subsequent
opt-in `robotd` integration and real-board/MuJoCo closed-loop tests are documented
in [HIL.md](HIL.md), including their unresolved locomotion failure. The robot's
control loop remains defined in [robotd-design.md](../../docs/design/robotd-design.md).

## Files and contract

The full-model follow-up, preparation and verification commands are documented in
[ALL-ACTIONS.md](ALL-ACTIONS.md). The single-model commands and results below are
the original baseline, not a restriction on the current panel.

For hardware sizing, see the separate [CPU, RAM and storage measurement](RESOURCE-USAGE.md).
That full-controller run is not the actor-only benchmark below.

For per-stage wall/CPU timing and the opt-in measurement harness, see
[STAGE-PROFILING.md](STAGE-PROFILING.md).

The later robotd NEON build correction and its matched-kernel validation / HIL
A/B results are recorded in [NEON-BUILD.md](NEON-BUILD.md).

- `policy.h` / `policy.c`: load the specialized weight file and infer 61 floats
  to 14 actions, preserving the project's observation/action order. No heap
  allocation or I/O occurs during inference. Non-finite inputs/outputs fail.
- `infer.c`: streaming executable accepting groups of 61 whitespace-separated
  floats on stdin and returning 14 raw policy actions per output line.
- `export.py`: checks topology, shapes, attributes, and FP32 types before exporting
  weights; generates ONNX Runtime CPU reference outputs for 5,120 observations.
- `bench.c`: verifies those outputs, warms up 200 iterations, then measures
  inference latency, process CPU, deadline misses, scheduling delay, sampled CPU
  frequency, and peak RSS. No measurements here include sensor/motor bus I/O.
- `serial_shell.py` / `run_board.py`: binary deployment over the existing USB
  gadget console, with SHA-256 validation, and repeatable target benchmarks.
- `verify_stream.py` / `stream-results.json`: deploy the streaming executable and
  check eight representative observations against the reference on the board.
- `results.json`: measured board results, source/model/tool versions, and hashes.
- `out/`: ignored generated models, weights, fixtures, and executable.

The exporter deliberately rejects other graphs, including LSTM policies. To
support a new topology, change the executor and validation together. The binary
weight format is an eight-byte `DUCKMLP1` header followed by little-endian FP32
arrays in `DuckPolicy` order; it is a bench format, not a public model API. Use
only exported, checksum-verified artifacts. It does not implement signed model
installation or the production policy loading contract.

## Reproduce on the Mac

From the repository root, install/use Zig and Python 3.12, then:

```sh
python3.12 -m venv experiments/imx6ull-policy/.venv
experiments/imx6ull-policy/.venv/bin/pip install \
  onnx==1.22.0 onnxruntime==1.30.0 numpy==2.5.3
mkdir -p experiments/imx6ull-policy/out
curl -fL --retry 3 \
  https://huggingface.co/pollen-robotics/microduck-policies/resolve/v5/velstand.onnx \
  -o experiments/imx6ull-policy/out/velstand.onnx
shasum -a 256 experiments/imx6ull-policy/out/velstand.onnx
```

The measured model's SHA-256 is
`1c659be55da94bc5753b707de5c6a3e7c49931e05ca3b6991615cef1a8ba9a45`.
Verify it before comparing against these results.

```sh
experiments/imx6ull-policy/.venv/bin/python experiments/imx6ull-policy/export.py \
  experiments/imx6ull-policy/out/velstand.onnx experiments/imx6ull-policy/out
zig cc -target arm-linux-musleabihf -mcpu=cortex_a7 \
  -O3 -ffp-contract=off -Wall -Wextra -Werror -static \
  experiments/imx6ull-policy/policy.c experiments/imx6ull-policy/bench.c \
  -lm -o experiments/imx6ull-policy/out/policy-bench-armv7
zig cc -target arm-linux-musleabihf -mcpu=cortex_a7 \
  -O3 -ffp-contract=off -Wall -Wextra -Werror -static \
  experiments/imx6ull-policy/policy.c experiments/imx6ull-policy/infer.c \
  -lm -o experiments/imx6ull-policy/out/policy-infer-armv7
python3 experiments/imx6ull-policy/run_board.py \
  --results experiments/imx6ull-policy/out/new-results.json
python3 experiments/imx6ull-policy/verify_stream.py
```

Tested compiler: Zig 0.16.0. The executable statically links musl and does not
require Python, a compiler, or a new glibc on the Debian 10 board. Do not use
`-ffast-math`: it would invalidate the non-finite safety checks.

The serial helper is scoped to this board's `/dev/cu.usbmodem1234fire56783`,
`debian` account, and `npi` login prompt. Close other serial terminals first.
It asks for the board password, or reads `MICRODUCK_BOARD_PASSWORD`. It installs
only into `/home/debian/microduck-policy-bench`; no root privilege is needed.
The CPU-contention case launches a bounded SHA-256 worker and stops it afterward.
No governor, boot configuration, or service is changed.

## Run again directly on the board

```sh
cd /home/debian/microduck-policy-bench
./policy-bench-armv7 weights.bin fixtures.bin 5000 0
./policy-bench-armv7 weights.bin fixtures.bin 3000 20
./policy-infer-armv7 weights.bin < observations.txt
```

The first is an unpaced throughput test; the second runs 3,000 scheduled ticks at
50 Hz, about 60 seconds. Every invocation first verifies the 5,120 reference cases.
Board output is JSON. `maxrss_native_units` is KiB on Linux (bytes on macOS).
CPU frequency is sampled every 50 iterations outside the inference timer.
Latency includes input checks and normalization, but not the preparation of the
synthetic observations, the reference validation, the warm-up, or model loading.
`deadline_misses` counts completion after the scheduled tick's 20 ms deadline,
including scheduler delay, and is meaningful only in paced runs. The percentile
statistics alone do not count every scheduling failure.

For the streaming executable, `observations.txt` contains one 61-float observation
per line in the project's original order. It keeps the model loaded across
requests. Its output is raw actions, **not servo positions**: the production
controller's scaling, home offsets, filters, and safety handling still apply.
Text parsing/formatting latency is not included in the C inference measurements.

## Numerical verification and limits

Fixtures use seed 20260919: 4,096 Gaussian observations with standard deviations
0, 0.1, 1, and 5, plus 1,024 nominal-gravity observations feeding the previous
reference actions back into the next input and varying forward/yaw commands.
Each of 71,680 action values must satisfy
`abs(actual-reference) <= 1e-4 + 1e-4*abs(reference)` against ONNX Runtime 1.30.0.
This is numerical agreement within tolerance, not bitwise identity. NaN/Inf input
rejection is also checked. The same validation runs on the actual ARM executable.

Synthetic action feedback is not a physics simulation. Passing these tests does
not establish stable walking, complete-loop timing, long-duration reliability,
or numerical agreement for every possible sensor input. Physical motor/IMU I/O
still needs validation; the simulated-body integration is covered in [HIL.md](HIL.md).

For the recorded outcomes and interpretation, see [RESULTS.md](RESULTS.md).
