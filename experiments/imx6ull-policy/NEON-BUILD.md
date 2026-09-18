# NEON build correction and A/B — 2026-09-19

The actual daemon now uses the existing NEON policy kernel. With identical HIL
workloads and stage probes, two interleaved runs per build reduced mean inference
wall time from **5.248 to 2.800 ms (46.6%)**, and whole measured tick time from
**10.791 to 8.715 ms (19.2%)**. No weights, graph, activation function, safety
threshold, control rate, governor or protocol were changed.

## Correction to earlier descriptions

The source contained a NEON implementation, but the Cargo-built ARM daemon did
not enable it. The compiler audit found cargo-zigbuild's wrapper selecting
`-mcpu=generic+v7a+vfp3-d32+thumb2-neon`. The existing `-mfpu=neon-vfpv4` did not
override that CPU's explicit `-neon`. Compiler macro output omitted `__ARM_NEON`,
and the policy object contained scalar `s`-register arithmetic instead of the
conditional vector path. The scalar daemon's hash matches the preceding
[stage profile](STAGE-PROFILING.md).

The standalone actor benchmark had already selected `-mcpu=cortex_a7`; its NEON
results were real, but did not prove that the separately built daemon used NEON.
Earlier descriptions that treated the two as the same implementation at runtime
were incorrect. Historical daemon timing/resource records remain valid for their
recorded binaries, not measurements of this corrected deployment.

Build policy and guard behavior belong to
[robotd-design.md §1](../../docs/design/robotd-design.md#1-the-shape-of-it).
For the corrected build, disassembly confirms vector loads and `q`-register
`vmul.f32` / `vadd.f32`. Keeping `-ffp-contract=off` means that a literal `vmla`
instruction is not required as evidence of NEON. Replaying the old ARM compiler
configuration with the new requirement fails with the intended diagnostic.

## Matched-kernel numerical validation

`bench.c` was linked against the **same Cargo-built `libduck_mlp.a`** used by the
new daemon, rather than independently recompiling `policy.c` with different flags.
[neon-policy-results.json](neon-policy-results.json) records all ten actors:

- 5,120 observations / 71,680 action values per actor.
- Total: **51,200 observations / 716,800 action values, zero tolerance failures**.
- NaN and Inf input rejection passed for every actor.
- Tolerance remains `abs(error) <= 1e-4 + 1e-4*abs(reference)`.
- Maximum absolute error was 0.0009765625, accepted by that combined tolerance;
  this is not bitwise equality or a universal 0.0001 absolute bound.

The verifier also performs a separate five-second 50 Hz actor-only timing run.
Those timings are not the HIL A/B below and must not be mixed with its averages.

SHA-256 provenance:

| Artifact | SHA-256 |
|---|---|
| Scalar daemon | `42aeff0df039ccf40fe87fc8890559af09bb910c4f2071464bdb99929fe334b1` |
| NEON daemon | `f34e02036e53c1e1bdd8b13ffba6bc24736a6484c8b1b2cd16ea5050fda6f4eb` |
| Cargo kernel archive | `348f8d36a3954cd54b8d183a4f7678b6fd23e32d2ddbb7b591f6658d84b6b0c8` |
| Benchmark linked to archive | `b49fe3779029b44280f3323ba5f0a7dc670333253f393e67a067c4604b732b0b` |

## Interleaved complete-loop A/B

[neon-ab-results.json](neon-ab-results.json) contains per-stage distributions,
per-policy inference distributions, health samples, hashes and board snapshots.
Execution order: scalar A1, NEON B1, scalar A2, NEON B2. The numerical verifier
ran between A1 and B1, so these are not identical thermal histories or a randomized
controlled trial. Both builds used alpha's seven resident actors, 20 Hz state
subscription, approximately 20 Hz commands, enabled stage probes, independent
headless Mac MuJoCo bodies and the same action schedule. The first five seconds
were excluded, then driving ticks selected. Stage semantics are defined in
[robotd-design.md](../../docs/design/robotd-design.md#optional-hil-stage-timing).

| Run | Driving ticks | Inference mean | Inference P99 | Tick mean | Tick P99 | Work / scheduled-finish >20 ms |
|---|---:|---:|---:|---:|---:|---:|
| Scalar A1 | 1,383 | 5.331 ms | 8.316 ms | 10.741 ms | 15.402 ms | 2 / 2 |
| NEON B1 | 1,383 | 2.760 ms | 3.450 ms | 8.675 ms | 13.094 ms | 0 / 0 |
| Scalar A2 | 1,387 | 5.165 ms | 8.366 ms | 10.841 ms | 15.595 ms | 1 / 2 |
| NEON B2 | 1,381 | 2.841 ms | 3.553 ms | 8.754 ms | 12.853 ms | 1 / 1 |

Sample-count-weighted means across each build's two runs:

| Metric | Scalar | NEON | Reduction |
|---|---:|---:|---:|
| Inference wall time | 5.248 ms | 2.800 ms | 46.6% (~1.87× speedup) |
| Whole measured tick wall time | 10.791 ms | 8.715 ms | 19.2% |
| Whole measured tick thread CPU | 6.860 ms | 4.586 ms | 33.1% |

At nominal 50 Hz, the last row represents about 34.3% versus 22.9% of one CPU
for the measured control-thread intervals, **not whole-process or system CPU**.
No new RAM or whole-board CPU sizing measurement was made. Model files are
unchanged. The loop remains 50 Hz; less work gives headroom, not a higher command rate.

The governor stayed `ondemand`, with before/after snapshots at 792 MHz; frequency
was not locked or measured continuously. Speedup therefore describes this complete
deployment change under its normal governor, not pure SIMD throughput at fixed
frequency. All four traces had no dropped rows or missing CPU samples, and the
steady samples covered all eight expected policy labels without `held`.

NEON B2 still had one 21.903 ms work-duration outlier. Thus **the improvement does
not establish hard-real-time behavior or zero deadline misses**. USB/proxy/host
round trips remain inside sensor/actuator wall times. Neither physical motors nor
the real sensor/servo bus were exercised.

## Action regression

After the A/B runs, the corrected daemon was exercised again with both `alpha`
and `velstand` in owned headless MuJoCo sessions. The compact telemetry evidence
is [neon-action-results.json](neon-action-results.json), derived with
`verify_actions.summarize` from ignored `out/neon-actions-board.json`.

Both profiles passed every expected policy transition, sit/rise latch check,
absence-of-`held` check and end-of-stage upright check. Alpha selected its
independent standing actor. Roll reached approximately 176.65° tilt on alpha
and 174.96° on velstand, then recovered; final recovery tilt was 0.31° and 0.59°.
This verifies simulated action execution and recovery, not kicking a physical
ball, picking up an object, or physical gait stability. Roller actors passed the
numerical suite, but roller closed-loop stability was not requalified and remains
the known unresolved issue. Low-command walking was not retrained or fixed.

```sh
.keyboard-venv/bin/python experiments/imx6ull-policy/verify_actions.py \
  --backend board --profile alpha --profile velstand \
  --results experiments/imx6ull-policy/out/neon-actions-repeat.json
```

## Build, verify, repeat

Use the toolchain and model/body prerequisites in [HIL.md](HIL.md#build-and-repeat)
and [ALL-ACTIONS.md](ALL-ACTIONS.md#reproduce). To ensure the numerical verifier
uses the daemon's compiled kernel, capture Cargo's actual build output directory
(the hash in this directory is not a stable interface):

```sh
cargo zigbuild --release --target armv7-unknown-linux-musleabihf \
  -p robotd --features imx6ull-mlp --message-format=json \
  > experiments/imx6ull-policy/out/neon-build.jsonl
mlp_dir=$(jq -r 'select(.reason == "build-script-executed" and (.package_id | contains("duck-control"))) | .out_dir' \
  experiments/imx6ull-policy/out/neon-build.jsonl)
test -f "$mlp_dir/libduck_mlp.a"
zig cc -target arm-linux-musleabihf -mcpu=cortex_a7 -O3 -ffp-contract=off \
  experiments/imx6ull-policy/bench.c "$mlp_dir/libduck_mlp.a" -lm \
  -o experiments/imx6ull-policy/out/policy-bench-cargo-neon
.keyboard-venv/bin/python experiments/imx6ull-policy/verify_policies.py --keychain \
  --benchmark experiments/imx6ull-policy/out/policy-bench-cargo-neon \
  --results experiments/imx6ull-policy/out/neon-policy-repeat.json
```

Stop on any build or numerical validation failure. Then copy the verified ARM
daemon to `out/robotd-hil` and follow the [stage profiling commands](STAGE-PROFILING.md#reproduce).
For A/B, retain the exact old executable separately; do not remove the new build
guard to regenerate an accidental fallback. Use unique output directories.
Raw A/B captures remain in ignored `out/neon-ab-scalar-a1`, `neon-ab-neon-b1`,
`neon-ab-scalar-a2`, and `neon-ab-neon-b2`.

The corrected NEON daemon is deployed in the existing board HIL directory and is
the local `out/robotd-hil` used on the panel's next board connection. No system
service or persistent profiling environment was installed. The scalar executable
is retained locally as `out/robotd-hil-scalar-profile` for rollback/comparison.

Build/host regression checks: eight Python profiling/build-guard tests passed;
robotd default 151 passed / 1 ignored, feature build 153 passed / 1 ignored;
duck-control feature tests 72 passed (eight additional ORT integration tests
ignored); keyboard regression suite 29 passed / one display test skipped.
The ARM release build succeeded. The isolated toolchain emitted a non-fatal
host build-script debug-stripping warning and a deprecated linker-option warning;
target linking and board execution completed successfully.
