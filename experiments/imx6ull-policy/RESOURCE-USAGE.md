# Full-controller resource usage — 2026-09-19

Measured on the attached Embedfire i.MX6ULL as a hardware-sizing reference,
not a minimum-spec guarantee. The board ran the real controller; an owned Mac
MuJoCo process supplied sensors and actuator responses. No physical motor/IMU
driver, camera, audio or wireless application workload was included.
The model/action acceptance boundaries are in [ALL-ACTIONS.md](ALL-ACTIONS.md).

## Workload and measurement

The alpha profile loaded seven actors: walking, independent standing, sit/stand,
ground-pick, left/right kick and roll. The approximately one-minute run exercised
idle, forward movement, stop, both kicks, ground-pick, roll, sit and rise. Models
stay resident, but only the selected actor is evaluated at each control tick.
All ten model files were installed on storage.

A bounded shell sampler was started before entering the USB tunnel. It read
`/proc/stat`, `/proc/uptime`, each process's `stat`, `status` and `smaps_rollup`,
`/proc/meminfo`, and `scaling_cur_freq`, approximately once per second. The
reported interior window contains 49 snapshots / 48 intervals over **51.78 s**,
excluding startup and shutdown. The raw capture was temporary; the aggregate
measurements are preserved in [resource-results.json](resource-results.json).

CPU percentages use process user+system tick deltas divided by elapsed uptime
and `CLK_TCK=100`. One fully occupied core is 100%. System busy is the aggregate
CPU tick delta excluding idle and I/O wait. Reported minima/maxima are interval
averages, **not instantaneous peaks**. The system figure includes sampling overhead.
The original `ondemand` governor was not changed or locked; every retained
frequency snapshot read 792 MHz, which does not prove the frequency never changed
between samples.

## Results

| Resource | Measurement |
|---|---|
| CPU | One Cortex-A7, ARMv7-A, 32-bit, NEON / VFPv4 |
| Frequency options | 198 / 396 / 528 / 792 MHz; sampled at 792 MHz in this run |
| Controller CPU | Mean 35.21%; interval range 34.26–36.45% |
| USB HIL proxy CPU | Mean 10.08%; interval range 9.26–11.11% |
| Whole-board CPU busy | Mean 54.60%; interval range 52.78–56.14% |
| Physical RAM / Linux-visible RAM | 512 MiB / 486.66 MiB |
| Controller resident RAM | Mean 9.40 MiB; sampled maximum 9.43 MiB, including seven loaded actors |
| USB HIL proxy resident RAM | 52 KiB |
| Whole-board RAM in use | About 129.37 MiB, defined as `MemTotal - MemAvailable` |
| RAM available | About 357.28 MiB |
| Storage device | 8 GB-class eMMC; reported user area 7,818,182,656 bytes (7.28 GiB) |
| Controller executable | 4.82 MiB |
| All ten model files | 7.55 MiB total; 791,592 bytes each |
| Controller + all models | 12.37 MiB; 13.62 MiB including the USB HIL proxy executable |
| Existing root filesystem + boot used blocks | About 619.88 MiB, including OS, tools, logs and experiments |

RAM uses the page-level `smaps_rollup` RSS rather than virtual address space.
On this kernel, the coarse `status` counters underreported residency (controller
`VmRSS=8440 KiB` versus page-level RSS `9588–9652 KiB`); the page-level PSS was
`9576–9640 KiB`. These measurements are not a launch-time peak or a memory-limit
test. Process RSS and whole-system use are different accounting views and should
not be added together. Storage byte counts exclude filesystem overhead unless
explicitly described as used blocks; MiB means 1,048,576 bytes.

Health samples were approximately 49.68–50.03 Hz. The cumulative missed-tick
counter increased from 2 to 21 between the first and last health samples, about
50 s apart. Thus this run is **not a zero-overrun or hard-real-time result**.
The memory sampler itself is part of the measured workload. No bus errors or
stale IMU blocks were reported; those sensors were simulated.

## Interpreting cost-reduction headroom

- The controller's working set is much smaller than the installed RAM. The
  current image also configures a **320 MiB CMA pool**; this is not memory owned
  by the controller, and its free pages are not all permanently unavailable.
  Reducing physical RAM requires reviewing the kernel/device-tree CMA setting,
  vendor services and intended peripheral workloads together, not subtracting
  process RSS from the board's nominal capacity.
- The roughly 620 MiB system image is not an unavoidable model requirement.
  Conversely, the 12.37 MiB program+weights figure is **not** a bootable Linux
  image or a flash-sizing recommendation. Bootloader, OS, filesystem overhead,
  configuration, logs and any A/B update/model slots require their own budget.
- The HIL proxy's CPU work can go away in a physical robot, but real sensor and
  motor I/O replaces it. Do not subtract its 10% and call the remainder a tested
  physical-controller load. CPU utilization at this sampled frequency also
  cannot establish performance at a lower frequency or on another architecture.
- ESP32-P4/S3 remain **feasibility candidates only**. No ESP32 binary, on-device
  benchmark, full control port or stability validation is included in this
  commit. Linux process sizes do not predict an MCU firmware's footprint, and
  quantizing an actor would require fresh numerical and closed-loop validation.

The next useful experiment is a separately authorized, fixed-frequency full-loop
test with worst-case timing and a representative sensor/motor workload. This
resource survey did not alter CPU frequencies, system services or boot settings.
