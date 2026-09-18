# Keyboard backend integration — 2026-09-19

The walk-only limitations in this historical integration record were superseded by
the [all-action follow-up](ALL-ACTIONS.md).

Imported keyboard worktree commits `644c804` and `7edce66` as `fd61da0` and
`342b17a`, then extended the panel with owned Mac / i.MX6ULL backend sessions.
User instructions belong to [Keyboard control](../../docs/robot/simulation.md#keyboard-control);
session ownership belongs to [the simulation design](../../docs/design/simulation.md#2-where-the-seam-is).

## Checks on this Mac and attached board

- Python regression suite: 19 tests passed. Covers keyboard repeats, command
  expiry, disconnect clearing, switch ordering, startup cleanup, unsupported
  board skills, rejecting a foreign listener, headless display fallback and
  forwarding body traffic during graceful board shutdown.
- Live headless board loop: health approximately 49.76–50.14 Hz, no bus read
  errors, two cumulative missed ticks (already present before movement).
  Sixty forward commands at 0.40 m/s over approximately three seconds changed
  the simulated trunk position to `[0.6654, 0.0320, 0.1193]` metres. This is a
  smoke test, not a controlled velocity-tracking benchmark.
- Live Tk panel with the actual MuJoCo viewer: Mac → board → Mac succeeded.
  Sampled control rates were 49.8–49.9 Hz, with zero missed ticks in these
  short GUI runs. Previous owned body processes and the board pump exited;
  closing the panel left no owned session.
- Injected a W keypress into the password field: it entered text without
  changing motion intent. Submitting a connection cleared the password field.
  Board kick buttons were disabled. GUI labels/layout were inspected through
  own-window screenshots; no global keyboard input was injected.
- Terminated only the owned headless body during a real board session:
  the panel worker reported disconnection, cleared command intent, stopped
  the bridge, released serial, and exited cleanly.
- Found an environmental viewer failure: CoreGraphics returned zero active
  displays and GLFW crashed in `_glfwGetVideoModeCocoa`. A subsequent launch
  waited in a Cocoa crash-recovery modal. The panel now detects no-display
  sessions and labels headless mode. Viewer launches ignore saved window state
  through process-local arguments, leaving global preferences untouched.
  After display availability returned, viewer-backed switching passed.
- Python compilation, shell launcher syntax and `git diff --check` passed.

Physical USB unplug/replug, long-duration loaded operation, physical motors,
and non-walking board skill policies were **not** validated. The existing
low-speed gait initiation / startup posture problems are not fixed here.
The initial benchmark in HIL.md remains a separate historical measurement.

## Remember-password follow-up

The board accepted a fresh `debian` login with the previously used password.
`passwd -S debian` returned `P`, confirming a password is set (not `NP`). No
password or login configuration was changed on the board.

The panel now exposes remember/forget controls backed by macOS Keychain. The
expanded Python suite passes 25 tests, including per-device separation,
reload in a new client, failed-login protection and keychain-failure handling.
Real Keychain save/read/delete passed with a unique temporary test entry.
The board's working credential was saved during a successful GUI connection;
switching Mac → board with the password field empty succeeded. A separate
fresh Python process also connected using only the saved credential. Own-window
screenshots verified the added checkbox/button layout. Restart an already-open
older panel to load these changes.

## Compact-window follow-up

The panel now has a 340×200 always-on-top mini view. It reuses the same Tk
window, input state, worker and backend session; expanding restores the prior
geometry and stacking mode. A real Mac/viewer session survived shrink/expand
without replacing its body process. Both layouts were inspected in own-window
screenshots. All 26 tests passed with `DUCK_GUI_TEST=1`, including a desktop-only
test for size/position restoration, held-key repeat suppression, fresh keypress
movement, the mini stop button and no extra backend connection during resizing.
