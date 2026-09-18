# i.MX6ULL gait inference results — 2026-09-19

The specialized FP32 `velstand` actor passed numerical validation and met the
50 Hz inference deadline in both 60-second paced tests on the attached Embedfire
board. This validates the actor on the CPU, not the complete physical robot.

These are the **initial inference-only measurements**. Subsequent real-board
`robotd`/MuJoCo closed-loop measurements and limitations are in [HIL.md](HIL.md).

## Hardware and software

- Single Cortex-A7, ARMv7/NEON; available CPU frequencies 198/396/528/792 MHz.
- 486 MiB visible RAM; Debian 10, Linux 4.19.35-imx6, glibc 2.28.
- Original `ondemand` governor retained throughout; no root access or persistent
  system changes. Temperature reported 54.5 °C before and 57.6 °C afterward.
- Zig 0.16.0, static ARM hard-float musl executable, Cortex-A7 target, `-O3`,
  `-ffp-contract=off`. No `-ffast-math`.
- FP32 ONNX graph specialized to C/NEON; **not** an ONNX Runtime ARM port.
  No quantization, pruning, or retraining.
- Model: official v5 `velstand.onnx`, 793,705 bytes, SHA-256
  `1c659be55da94bc5753b707de5c6a3e7c49931e05ca3b6991615cef1a8ba9a45`.
- Architecture 61 → 512 → 256 → 128 → 14, three ELUs, 197,896 stored FP32
  values including normalization, 196,864 dense multiply-accumulates per call.

## Actual board measurements

All times below measure input checking, normalization, dense/ELU computation,
and output checking, excluding sensor/motor I/O and the text streaming wrapper.

| Test | Calls | Mean | P99 | Maximum | Missed 20 ms deadline |
|---|---:|---:|---:|---:|---:|
| Unpaced, sampled at 792 MHz | 5,000 | 1.733 ms | 1.971 ms | 2.987 ms | N/A |
| 50 Hz, 60 s, original ondemand governor | 3,000 | 3.765 ms | 5.789 ms | 9.577 ms | 0 |
| 50 Hz, 60 s, concurrent SHA-256 CPU worker | 3,000 | 1.722 ms | 1.732 ms | 13.243 ms | 0 |

The unpaced test had no inference over 20 ms but has no scheduled deadline.
The ordinary 50 Hz test consumed 19.38% process CPU and sampled frequencies from
198 to 792 MHz. Peak process RSS was 1,460 KiB, about 1.43 MiB.

The competing worker kept the CPU at 792 MHz. Consequently most inference calls
were faster than under idle/downclocked operation, while one preemption produced
a 13.243 ms outlier. Process CPU for the actor was 9.01%; the worker's CPU is not
included in that percentage. This is one specific contention workload, not proof
against every background service or I/O interrupt pattern. The worker was stopped
and verified absent afterward.

Paced-run deadline accounting includes late wake-up and inference completion
relative to each scheduled tick. Maximum start lateness was 7.348 ms in the
ordinary run and 0.617 ms with the worker; both runs still completed every tick
before its next 20 ms boundary. Do not add independently observed maximum
lateness and maximum inference as though they necessarily occurred together.

## Correctness checks

The ARM executable checked 5,120 synthetic observations against ONNX Runtime
1.30.0 on the Mac, covering 71,680 action values. All passed
`abs_error <= 1e-4 + 1e-4*abs(reference)`; maximum absolute error was
0.000106812. This is tolerance-based agreement, not bitwise identity or a claim
that every absolute error was below 0.0001. NaN/Inf inputs were rejected.

Mac AddressSanitizer/UndefinedBehaviorSanitizer validation also passed. Invalid
weight files, empty reference files, and invalid iteration counts were rejected.
The separate streaming executable was deployed and verified using selected
reference inputs; its results are in `stream-results.json`.

## Installed artifacts and limits

Board directory: `/home/debian/microduck-policy-bench/`. It contains the original
ONNX model, extracted weights, reference fixtures, model metadata, C sources,
the benchmark, and the streaming inference executable. Re-run:

```sh
cd /home/debian/microduck-policy-bench
./policy-bench-armv7 weights.bin fixtures.bin 3000 20
./policy-infer-armv7 weights.bin < observations.txt
```

The evidence supports using this board for further motion-control integration:
the actor alone fits a 20 ms period with useful headroom in these tests. It does
not yet establish a 50 Hz complete loop with servo/IMU transfers, or stable
walking on hardware. At this initial measurement stage, `robotd` had not yet
been ported; the subsequent opt-in integration is recorded in [HIL.md](HIL.md).
Physical robot testing and longer-duration jitter testing remain separate work.

Sources and reproduction: [README.md](README.md). Machine-readable observations:
[results.json](results.json) and [stream-results.json](stream-results.json).
