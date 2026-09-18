# Microduck i.MX6ULL 实验扩展

本仓库基于 [Pollen Robotics 的 Microduck](https://github.com/pollen-robotics/microduck)，
由 TonyRuan 维护 i.MX6ULL 运控移植与 Mac 仿真遥控的实验扩展，**不是上游官方发行版**。
机器人平台、原始控制软件及步态策略来自上游项目；保留其项目介绍、署名与 [LICENSE](LICENSE)。

## 本仓库新增

- **i.MX6ULL 全动作推理**：将当前 v5 策略集的 10 个 FP32 网络专门化为 C/NEON 实现并在野火开发板上验证；
  这不是通用 ONNX Runtime 的 ARM 移植。见[全动作移植实测](experiments/imx6ull-policy/ALL-ACTIONS.md)。
- **完整运控硬件在环**：真实开发板运行 `robotd` 控制链路，Mac 上的 MuJoCo 提供模拟传感器和电机响应。
  见[闭环实测与边界](experiments/imx6ull-policy/HIL.md)。
- **Mac 键盘遥控 GUI**：支持 Mac / 板端后端切换、USB 与控制服务状态、速度预设、钥匙串记住密码，
  三组策略配置、P 键捡拾／轮式蹲伏，以及 340×200 置顶迷你模式。
  见[启动和操作说明](docs/robot/simulation.md#keyboard-control)。

## 当前验证范围

短时硬件在环测试中，完整控制闭环约为 **50 Hz**，但有过超时采样，不能视为零超时或长期实时性保证；
性能口径与数据见 [HIL 报告](experiments/imx6ull-policy/HIL.md#measurements)。
**真实电机与物理传感器尚未验证**，也不代表原版机器人的全部功能已经移植到这块板。

板端现已接入站立、行走、坐起、翻滚、左右踢球、捡拾及轮式／蹲伏模型；
**足式动作通过短时闭环测试，轮式稳定性尚未通过验收**，仍保留为实验模式。
模型一致性、闭环动作切换与真实物理任务成功是不同的验收层级，见[验证范围](experiments/imx6ull-policy/ALL-ACTIONS.md)。
低速起步困难与启动回零前倾仍未解决，见[行为诊断](experiments/imx6ull-policy/DIAGNOSIS.md)。
本仓库的 GitHub Actions 当前未启用；下方 CI 徽章仅代表上游仓库。

## 从这里开始

- 使用键盘面板：[环境准备、启动与后端切换](docs/robot/simulation.md#keyboard-control)。
- 复现移植实验：[推理构建](experiments/imx6ull-policy/README.md)与[完整闭环构建](experiments/imx6ull-policy/HIL.md#build-and-repeat)。
- 查看 GUI 集成与回归验证：[测试记录](experiments/imx6ull-policy/GUI-INTEGRATION.md)。
- 查看硬件降本参考：[CPU、RAM 与存储实测](experiments/imx6ull-policy/RESOURCE-USAGE.md)。

---

## 上游项目介绍 / Upstream README

以下保留上游 Microduck 的项目介绍。其 RK3566 平台、演示视频和功能说明描述的是上游机器人，
不应理解为本仓库 i.MX6ULL 实验的验收结果。

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
  上游 CI / Upstream CI（非本仓库）：
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
