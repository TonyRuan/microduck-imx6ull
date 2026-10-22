# Microduck i.MX6ULL Experimental Extension

[English](README.md) | [简体中文](README.zh-CN.md)

This repository is based on [Pollen Robotics' Microduck](https://github.com/pollen-robotics/microduck).
TonyRuan maintains experimental extensions for i.MX6ULL locomotion control and Mac simulation remote control. **This is not an official upstream release.**
The robot platform, original control software, and gait policies come from the upstream project; its introduction, credits, and [LICENSE](LICENSE) are preserved.

## Additions in this repository

- **i.MX6ULL inference for all actions:** the 10 FP32 networks in the current v5 policy set are specialized into C/NEON implementations and validated on a Wildfire development board.
  This is not a port of a general-purpose ONNX Runtime to ARM. See the [all-action port measurements](experiments/imx6ull-policy/ALL-ACTIONS.md).
- **Hardware-in-the-loop testing of the full controller:** a real development board runs the `robotd` control chain, while MuJoCo on a Mac provides simulated sensor readings and motor responses.
  See the [closed-loop measurements and limitations](experiments/imx6ull-policy/HIL.md).
- **Mac keyboard remote-control GUI:** switch between Mac and board backends; view USB and control-service status; use speed presets and Keychain password storage;
  choose among three policy configurations; use P for pick-up / wheeled crouching; and enable a 340×200 always-on-top mini mode.
  See the [startup and control instructions](docs/robot/simulation.md#keyboard-control).

## Current validation scope

In short hardware-in-the-loop tests, the complete control loop ran at about **50 Hz**, with some timeout samples. This is not a guarantee of zero timeouts or long-term real-time performance.
See the [HIL report](experiments/imx6ull-policy/HIL.md#measurements) for measurement definitions and data.
**Real motors and physical sensors have not been validated.** These results also do not mean that every feature of the original robot has been ported to this board.

The board backend now integrates standing, walking, sit/stand, rolling, left/right kicks, pick-up, and wheeled / crouching models.
**Legged actions passed short closed-loop tests; wheeled stability has not passed acceptance testing** and remains experimental.
Model equivalence, closed-loop action switching, and success at real physical tasks are separate acceptance levels; see the [validation scope](experiments/imx6ull-policy/ALL-ACTIONS.md).
Difficulty starting at low speeds and forward tipping during startup zeroing remain unresolved; see the [behavior diagnosis](experiments/imx6ull-policy/DIAGNOSIS.md).
GitHub Actions is currently disabled in this repository; the CI badge below refers only to the upstream repository.

## Start here

- Use the keyboard panel: [environment setup, startup, and backend switching](docs/robot/simulation.md#keyboard-control).
- Reproduce the port experiments: [inference build](experiments/imx6ull-policy/README.md) and [full closed-loop build](experiments/imx6ull-policy/HIL.md#build-and-repeat).
- Review GUI integration and regression validation: [test record](experiments/imx6ull-policy/GUI-INTEGRATION.md).
- Review hardware cost-reduction data: [CPU, RAM, and storage measurements](experiments/imx6ull-policy/RESOURCE-USAGE.md).

---

## Upstream project introduction / Upstream README

The upstream Microduck introduction is preserved below. Its RK3566 platform, demonstration videos, and feature descriptions refer to the upstream robot;
they are not acceptance results for this repository's i.MX6ULL experiments.

<p align="center">
  <img src="https://github.com/user-attachments/assets/c2f7c245-8217-46a1-8d1e-e0ba967cd969" alt="microduck" width="820">
</p>

<h1 align="center">Microduck</h1>

<p align="center">
  <em>A tiny biped robot that moves using reinforcement learning policies.</em>
</p>

<p align="center">
  <a href="https://pollen-robotics.com/microduck"><b>Get yours here</b></a> ·
  <a href="docs/robot/cheatsheet.md">Cheat sheet</a> ·
  <a href="https://github.com/pollen-robotics/microduck_rl">Training the policies</a> ·
  <a href="docs/design/architecture.md">How it works</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

<p align="center">
  Upstream CI (not this repository):
  <a href="https://github.com/pollen-robotics/microduck/actions/workflows/ci.yml"><img src="https://github.com/pollen-robotics/microduck/actions/workflows/ci.yml/badge.svg" alt="Upstream CI"></a>
</p>

---

**This repo is the duck's brain.** About 25 cm and 800 g of robot, run by a handful of daemons on a
Rockchip RK3566: a 50 Hz control loop driving fifteen servos from neural policies, the radios and
the camera, and the update machinery that gets new software onto a robot without bricking it.

Everything you need to run a Microduck is here. **If you want one,
[get yours here](https://pollen-robotics.com/microduck).**

The policies it runs are trained next door, in
**[microduck_rl](https://github.com/pollen-robotics/microduck_rl)** — MuJoCo and PPO, the sim2real
recipe, and the export to ONNX that this repo loads.

## It does things

<table>
<tr>
<td width="50%">
  <video src="https://github.com/user-attachments/assets/356a6011-8e0d-4b28-bda9-da78646583a3" controls width="100%"></video>
</td>
<td width="50%">
  <video src="https://github.com/user-attachments/assets/abfbf250-1b1c-42cb-8430-00267e2b148a" controls width="100%"></video>

</td>
</tr>
<tr>
<td><b>It walks.</b> Pick up a gamepad and drive.</td>
<td><b>It rolls.</b> Put wheels on, hold D-pad up, and it loads the other brain.</td>
</tr>
<tr>
<td width="50%">
  <video src="https://github.com/user-attachments/assets/7e70c1da-e120-428f-ae0b-f4de62f25984" controls width="100%"></video>
</td>
<td width="50%">
  <video src="https://github.com/user-attachments/assets/3eef63a5-6f84-47cf-90de-e717e6d7f8f0" controls width="100%"></video>
</td>
</tr>
<tr>
<td><b>It picks things up.</b> Beak to the floor, one button.</td>
<td><b>It gets back up.</b> Knock it over and it stands itself up.</td>
</tr>
</table>

It also sits, kicks a ball, rolls forward on command, and quacks in a voice that is its own.

## Where to find things

### You have a duck

| | |
|---|---|
| [Cheat sheet](docs/robot/cheatsheet.md) | Every `robotctl` command: drive, configure, voice, chorale, theremin, wifi, updates, logs. Start here. |
| [Gamepad](docs/robot/cheatsheet.md#gamepad-configd) | The full button mapping, and pairing a pad — [once per pad](docs/robot/pair-a-gamepad.md), plus what to do when it will not bond. |
| [`duckctl`](docs/robot/duckctl.md) | The robot from a laptop over Bluetooth, with no network and no ssh. |
| [Updates](docs/robot/cheatsheet.md#updates-updaterd) | Install, roll back, pin. Every update is verified, health-gated and reversible. |

### You are building on it

| | |
|---|---|
| [microduck_rl](https://github.com/pollen-robotics/microduck_rl) | Where the policies come from: MuJoCo, PPO, domain randomisation, and the ONNX export this repo loads. |
| [How it works](docs/design/architecture.md) | The whole system on one page — the daemons, the bus, how an update reaches a robot — then a page per part. |
| [Set up a dev board](docs/robot/install-dev.md) | From a blank board to a robot that takes branch builds. |
| [Dev cheat sheet](docs/robot/cheatsheet-dev.md) | Branch builds, release candidates, driving from a laptop, and the restart traps after an update. |
| [Push your branch](docs/robot/dev-push.md) | Build on your machine, install over ssh, about a minute. |
| [The simulated duck](docs/robot/simulation.md) | No robot on the desk? `scripts/duck-sim` runs the real daemons against a body in MuJoCo — one duck in a window, or four as machines you log into. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Building, testing, layout, conventions, releasing. |
| [Docs index](docs/README.md) | Everything, including the design pages and the open problems. |

## Under the hood

Rust, no framework, one workspace. `robotd` owns the control loop and the motor bus; `updaterd`
installs signed releases and rolls them back when a robot comes up unhealthy; `configd` owns wifi
and identity; `btd` is the Bluetooth path a phone uses; `padd` reads the gamepad; `mediad` streams
the camera over WebRTC; `tofd` serves the depth sensor. They talk over one JSON-RPC contract on
Unix sockets, and every client — the app, the console, the gamepad, your script — sends exactly the
same calls.

The interesting decisions are written down: [`docs/design/`](docs/design/) is why things are the
way they are, and [`docs/project/`](docs/project/) is what has gone wrong and what would close it.

## A note on ducks

No duck was harmed in the making of this robot. Several were consulted.
