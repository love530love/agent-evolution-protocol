# Qoder Fail-Closed 值守语义审查 · 2026-11-22

## 0. 请求与回执

- 请求事件：`[AEP-ACK:20261122T034130140395Z-bef2fe82]`
- 挑战回显（仅证明收到本条带校验串的请求，不是投递或任务回执）：

```text
AEP-ACK: CF7E352CBD583C
```

- 审查者：Qoder 原会话。会话标识 `713abe76-2b84-414f-b63b-3961345f6c78` 来自 UIA 转录文本，属于定位证据，不等同于独立身份认证。
- 审查范围：只读审计 `resident_status.py`、`supervisor.py`、`core_host.py` 的值守状态语义，并审阅当时未提交的 fail-closed 改动。
- 边界：未修改 `commander_uia.py`、`uia3.py`、`tests/test_uia3_driver.py` 或其他既有未提交 UIA 文件；未触碰 `hal-audit.txt`、`uia_receipts/`；未启停任何常驻进程；未做实机发送、未提交、未推送。本文件是本次审查唯一新增文件。

## 1. 审查时点与仓库状态

- 审查开始时 HEAD 为 `072604e`，三个值守模块及其测试处于未提交状态。
- 审查进行中，HEAD 前进到 `d42030e fix: make resident delivery state fail closed`：
  - 作者：`love <love@workbuddy.ai>`，时间：`2026-11-22 11:44:24 +0800`。
  - 变更：6 个文件，71 insertions / 22 deletions（`core_host.py`、`supervisor.py`、`resident_status.py` 及对应三个测试）。
- 逐 blob 复核工作树与提交一致（本机对象哈希）：
  - `core_host.py`：`db3570e8c66be19e37aa5b4d83dc1870b987fb89`
  - `supervisor.py`：`c2b19234d08768423bd93712986b3ed264f49869`
  - `resident_status.py`：`5221515541b6be367a4fd8c2cde316573ae62f22`
  - `git diff --stat HEAD -- <上述六文件>` 为空，因此下面针对工作树内容的结论对 `d42030e` 同样成立。
- 仓库状态：`main...origin/main [ahead 1]`，即 `d42030e` 仅存在于本地，尚未推送，远程 CI 未运行。
- 仍未提交：`commander_uia.py`、`uia3.py`、`tests/test_uia3_driver.py`（3 文件，71 insertions / 9 deletions），以及未跟踪的 `OVERNIGHT_20261121.zh-CN.md`、`hal-audit.txt`、`uia_receipts/`。

## 2. 总体结论

1. **三个值守模块的 fail-closed 语义成立，本次审查通过（有残余风险）。**
   - Core Host 的任何心跳都强制声明没有桌面手、没有派发、没有交付主张。
   - Supervisor 心跳区分 `dispatch` / `observe-only`，并对全部路径（含异常 tick）声明 `delivery_claim: "NONE"`。
   - `resident_status` 对缺失或未知的桌面模式收敛为 `UNVERIFIED` 并给出 `HOLD`，不再把“读不到模式”解释为“可以输入”。
   - `READY` 只表示常驻循环健康且桌面宿主处于 dispatch 模式，不证明任何消息已送达或已 ACK。
2. **未提交的 UIA 改动方向正确：**忙碌判定不再让转录文本和历史/隐藏停止按钮把客户端钉在 `busy`，`route_gate` 完整性判据进入回执。定向回归通过，但归属未确认，且缺少真实 Qoder 0.5.13 的实机复验证据，不应由本次审查代为提交。
3. **本次审查未修改任何代码。** 发现两处值得后续小修的点（陈旧心跳的 `desktop_mode` 暴露、失败 tick 的测试缺口），均记录在风险节。
4. **没有发生真实派工、ACK 或额度耗尽推断。** 本机当前 Desktop Host 仍为 `observe-only`。

## 3. 值守语义审计

### 3.1 Core Host（`src/agent_evolution_protocol/coordination/core_host.py`）

- 定位与边界（3-15 行）：Core Host 只做持久化的 presence、信箱、截止时间、订单沉积与联邦推进；明确不拥有桌面手。Windows Session 0 服务看不到登录用户窗口，这一分离避免把“服务活着”误读为“已输入/已确认”。
- 心跳不变量（41-52 行）：`_heartbeat()` 在 `record.update(...)` 中强制写入
  - `ui_touched: false`
  - `delivery_claim: "NONE"`
  - `desktop_dispatch_allowed: false`
  - `dispatch_performed: false`

  无论调用方 payload 带什么，最终落盘值都不能表达“碰过桌面”或“有交付主张”。
- 单实例（55-82 行）：使用真实持有的文件锁（Windows `msvcrt.locking`，POSIX `fcntl.flock`），不是“PID 是否存活”的启发式；锁在 `finally` 释放。第二个实例得到 `AlreadyRunning`，`main()` 以退出码 0 报告（179-181 行），不产生第二个循环。
- 单轮语义（85-114 行）：`cycle()` 只调用 `campaign_tick.tick`、`mission_pulse.pulse(apply=True)` 和 `federation.step(dispatch=False)`；若联邦结果意外带有真值 `sent`，抛出 `CORE_HOST_DISPATCH_VIOLATION`（112-113 行），把“联邦实现变化偷偷给 Core Host 长出手”变成显式故障。
- 循环与证据（117-159 行）：STARTED 与每个 tick 都先写原子心跳，再把**同一返回记录**打印到 stdout（131-134、155-156 行），文件和标准输出不再可能各说一套；异常被捕获为 `CYCLE_FAILED` 并保留错误文本（149-154 行），避免静默死亡。
- 残余注意点：`_heartbeat` 的 `**payload` 位于 `schema`/`pid` 之后（42-45 行），理论上调用方 payload 可覆盖这两个字段；当前内部调用方不传，风险低。
- 守卫测试：`tests/test_core_host.py:32-50` 同时断言心跳文件与最后一条 stdout tick 含 `desktop_dispatch_allowed=false`、`dispatch_performed=false`、`delivery_claim="NONE"`（42-49 行）。

### 3.2 Supervisor / Desktop Host（`src/agent_evolution_protocol/coordination/supervisor.py`）

- 两种模式（78-112 行）：
  - `dispatch=True`：调用 `commander.tick`，可能触碰窗口并尝试派发。
  - `dispatch=False`（`--observe-only`）：只调用 `commander.check_acks(root)`，读取已到达的 ACK 证据并更新持久订单状态，不聚焦窗口、不发现输入框、不输入文本（81-88 行文档串；110-112 行分支）。
- 心跳语义（32-44 行）：
  - 所有记录（STARTED、TICK_COMPLETED、TICK_FAILED）都写 `delivery_claim: "NONE"`。
  - `dispatch_enabled` 由 `mode == "dispatch"` 推导；没有 `mode` 时自然为 `false`，方向 fail-closed。
  - 即使 `dispatched` 非空，也只代表尝试列表，不代表送达或接受（39-40 行注释）。
- 异常路径补齐（124-134 行）：`TICK_FAILED` payload 现在也携带 `mode`（131 行），异常心跳不再因缺少模式而被读成“未知可派发”；记录仍以同一对象打印到 stdout（133-134 行）。
- observe-only 的只读性已沿调用链核实：`commander.check_acks`（`commander.py:579-621`）只做判定与状态迁移，注释明确“read-only and never replays input”（594-595 行）；它不调用 `dispatch()`。
- 守卫测试：`tests/test_supervisor.py:43-57` 断言 observe-only 模式不调用 `commander.tick`、只调用一次 `check_acks`，且心跳文件与 stdout 都含 `mode=observe-only`、`dispatch_enabled=false`、`delivery_claim=NONE`（52-57 行）。

### 3.3 resident_status（`src/agent_evolution_protocol/coordination/resident_status.py`）

- 健康判定（28-47 行）：
  - 文件缺失 → `MISSING`；JSON 非法或非对象 → `INVALID`。
  - 时间戳必须带时区；无时区或不可解析 → `INVALID / HEARTBEAT_TIMESTAMP_INVALID`。
  - 未来时间戳 → `INVALID / HEARTBEAT_TIMESTAMP_IN_FUTURE`（42-44 行），不会因“时间更新”而续命。
  - 只有 `STARTED`、`CYCLE_COMPLETED`、`TICK_COMPLETED` 且在 `max_age_seconds` 内才算健康（45 行）；`TICK_FAILED`、`CYCLE_FAILED` 一律 `STALE_OR_FAILED`。
- 必选与可选项（67 行）：core、desktop 必选；elevated 是一次性工作器，缺失属正常，不拖垮整体判定（62-66 行注释）。
- 模式 fail-closed（68-73 行）：缺失、非字符串或不在 `{"dispatch","observe-only"}` 的模式统一收敛为 `UNVERIFIED`；随后 74-81 行给出：
  - 任一必选平面不健康 → `HOLD`
  - 健康且 `observe-only` → `OBSERVE_ONLY`
  - 健康且 `dispatch` → `READY`
  - 其他（`UNVERIFIED`）→ `HOLD`
- 输出语义（82-91 行）：额外返回 `desktop_mode`；始终 `delivery_claim: "NONE"`、`execution_started: false`，并注明 `READY` 不代表送达或 ACK（89 行）。
- 参数校验（53-55 行）：`max_age_seconds` 拒绝布尔、非有限值和非正数，避免“无限宽限”。
- 守卫测试：`tests/test_resident_status.py:15-24`（READY/desktop_mode）、`27-35`（缺失/陈旧 → HOLD）、`38-44`（observe-only）、`47-57`（模式缺失/空串/未知 → HOLD + UNVERIFIED）、`60-68`（未来时间戳）、`71-74`（非法阈值）。

### 3.4 交付真值链语义

三个模块均未声称 `WORKSPACE_ATTESTED`、`INPUT_SUBMITTED`、`SEND_CONFIRMED`、`MESSAGE_VISIBLE`、`AGENT_ACK` 或 `TASK_RESULT_READBACK`。心跳/常驻状态只回答“循环是否活着、处于什么模式”；最终交付主张仍只属于订单回执与锚定的 AEP-ACK 证据。本次审查支持这一分层，没有把任何运行读数升级为投递结论。

## 4. 未提交 UIA 改动的只读审阅

### 4.1 `uia3.py` 忙碌判定（752-796 行）

- 问题背景（756-763 行注释）：Qoder CN 0.5.12 上，屏幕外回复文本中的“执行中”会使 `activity_state=busy`，导致严格派工在输入前持续延后；历史转录被误当成客户端状态。
- 改动：
  - 排除 `Text` 控件、超过 24 字符的名称、不可见控件（768-778 行）。
  - `BUSY_PATTERNS`/`READY_PATTERNS` 从子串匹配改为 `startswith`，`BUSY_EXACT` 仍做精确匹配（783-793 行）。
  - 命中去重（788 行）。
- 调用点：`observe()` 把结果写入 `activity_state`（190-191 行）；`commander.tick` 仅在 `activity_state == "ready"` 时恢复被延后的订单（`commander.py:795-805`），因此误判 `busy` 会阻塞派工，误判 `ready` 才可能导致在忙碌时输入，后者更危险。
- 测试：`tests/test_uia3_driver.py:184-200`（转录文本 + 隐藏停止按钮 → ready）、`202-213`（可见停止按钮 → busy）。定向回归通过。
- 残余风险：名称长度 >24 的**可见停止按钮**会被整体跳过，属于“漏报 busy”的方向（可能过早 ready）。当前 Qoder 实测控件名较短，但建议补一个长名称停止按钮用例，或对 `BUSY_EXACT` 放宽长度限制。

### 4.2 `commander_uia.py` 回执透传（169-175 行）

- 新增 `route_gate` 进入回执键列表（173 行），与 `drivable`、`blocked_by`、`session_lease`、`session_attestation` 等完整性判据一起持久化。
- `route_gate` 的来源：`ui_fallback.py:1290` 在严格有界区域路径写入 `"strict-bounded-region"`。
- 效果：派工被拒时，订单历史能直接看到“是严格路线门拒的”，不必再从 `reason` 反推。无放宽或绕过逻辑。

### 4.3 归属与提交边界

- 这三件套在本次审查开始前已是未提交状态；`OVERNIGHT_20261121.zh-CN.md`（07:55、08:00 节）记录其归属仍待原作者或用户确认。
- 本次审查只读，不改、不提交。建议：归属确认后，连同真实 Qoder 0.5.13 的 observe/校准证据一起审阅再提交；不要把“43 项测试通过”当成实机验收。

## 5. 实测证据

### 5.1 定向回归

命令与结果（项目运行时 Python 3.13.9，`PYTHONUTF8=1`）：

```text
./.tools/agent-runtime/Scripts/python.exe -m pytest -q \
  tests/test_core_host.py tests/test_resident_status.py \
  tests/test_supervisor.py tests/test_uia3_driver.py
........................................... [100%]
43 passed in 1.36s
```

### 5.2 真实协调根只读读数

`resident_status.status(coordination)` 在 `2026-11-22T03:44:34.989323+00:00`：

| 平面 | 状态 | 心跳状态 | 模式 | age |
| --- | --- | --- | --- | --- |
| core | HEALTHY | CYCLE_COMPLETED | — | 18.703 s |
| desktop | HEALTHY | TICK_COMPLETED | observe-only | 20.111 s |
| elevated | MISSING | — | — | — |

汇总：`control_plane = OBSERVE_ONLY`，`desktop_mode = observe-only`，`delivery_claim = NONE`。此处只读调用没有启停任何常驻进程。

### 5.3 模式判定探针（隔离临时目录）

| 场景 | control_plane | desktop_mode | delivery_claim |
| --- | --- | --- | --- |
| 健康、无 mode | HOLD | UNVERIFIED | NONE |
| 健康、observe-only | OBSERVE_ONLY | observe-only | NONE |
| 健康、dispatch | READY | dispatch | NONE |
| 陈旧 180 s、dispatch | HOLD | dispatch | NONE |
| TICK_FAILED、dispatch | HOLD | dispatch | NONE |

前三行证明 fail-closed 主路径正确；后两行暴露残余风险 R2。

## 6. 风险与建议

| 编号 | 等级 | 风险 | 建议 | 本次是否修改 |
| --- | --- | --- | --- | --- |
| R1 | 中 | 心跳文件是同用户可写的本地 JSON，无签名或进程认证；`READY` 可被伪造 | 明确“心跳只是部署读数，不是收据”；如未来需要强证明，加入进程启动标识或签名，禁止据此跳过订单回执 | 否 |
| R2 | 中低 | 陈旧/失败心跳仍把 `desktop_mode` 报告为 `dispatch`（实测见 5.3），只看该字段的看板可能误读为“可派发” | 仅在 desktop 平面健康时暴露模式，否则输出 `UNVERIFIED`；或增加“模式来自陈旧心跳”的显式字段 | 否 |
| R3 | 低 | 缺少 `TICK_FAILED` 心跳含 `mode`/`dispatch_enabled` 的测试，也缺少 `CYCLE_FAILED` 强制标志的测试 | 各补一条最小回归，锁住异常路径语义 | 否 |
| R4 | 中（运维） | 计划任务 `ExecutionTimeLimit=PT23H`；Desktop 的 `schtasks` 备用注册无 `RestartOnFailure`（见 OVERNIGHT 07:25/07:30/07:45） | 修复两个注册分支并做终止/重启实测；这是长时值守缺口，fail-closed 改动不覆盖 | 否 |
| R5 | 低 | UIA 忙碌判定跳过名称 >24 字符的控件，可能是漏报可见停止按钮 | 补长名称停止按钮用例；或对精确停止词不放宽长度限制 | 否 |
| R6 | 流程 | UIA 三件套仍未提交且归属未确认 | 归属确认 + 实机校准后再提交；本次审查不代为提交 | 否 |
| R7 | 流程 | `d42030e` 仅在本地，`ahead 1`，远程 CI 未跑 | 推送与 CI 需所有者/用户授权后执行，并核对 `main...origin/main` | 否 |
| R8 | 低 | `resident_status` 由 `mode` 推导 `READY`，未交叉校验心跳中的 `dispatch_enabled` 布尔值 | 后续可要求 `dispatch_enabled is True` 才给 `READY`，双字段一致更稳 | 否 |

## 7. 最小验证命令（PowerShell 7）

在仓库根目录执行：

```powershell
Set-Location K:\PythonProjects5\agent-evolution-protocol
$env:PYTHONUTF8 = '1'

# 1) 定向回归，期望 43 passed
& .\.tools\agent-runtime\Scripts\python.exe -m pytest -q `
  tests/test_core_host.py tests/test_resident_status.py `
  tests/test_supervisor.py tests/test_uia3_driver.py

# 2) 真实值守只读读数；期望 delivery_claim 恒为 NONE
pwsh -NoProfile -File .\scripts\aep-resident-status.ps1 `
  -CoordRoot .\coordination -MaxAgeSeconds 90
```

隔离的 fail-closed 探针（不触碰真实协调区，期望 `HOLD UNVERIFIED NONE`）：

```powershell
$probe = Join-Path $env:TEMP ('aep-failclosed-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $probe 'runtime') -Force | Out-Null
$now = [DateTime]::UtcNow.ToString('o')
Set-Content -LiteralPath (Join-Path $probe 'runtime\aep-core-host.heartbeat.json') `
  -Value ('{"status":"CYCLE_COMPLETED","completed_at":"' + $now + '"}') -Encoding utf8NoBOM
Set-Content -LiteralPath (Join-Path $probe 'runtime\aep-supervisor.heartbeat.json') `
  -Value ('{"status":"TICK_COMPLETED","completed_at":"' + $now + '"}') -Encoding utf8NoBOM
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
& .\.tools\agent-runtime\Scripts\python.exe -c `
  "import sys; from pathlib import Path; from agent_evolution_protocol.coordination.resident_status import status; r=status(Path(sys.argv[1])); print(r['control_plane'], r['desktop_mode'], r['delivery_claim'])" $probe
Remove-Item -LiteralPath $probe -Recurse -Force
```

把 supervisor 心跳依次改为带 `"mode":"observe-only"` 与 `"mode":"dispatch"`，应分别得到 `OBSERVE_ONLY observe-only NONE` 与 `READY dispatch NONE`。

## 8. 下一步

1. 由 UIA 改动归属方确认三件套，并在真实 Qoder 0.5.13 上补一次 observe/校准证据；确认后再审阅提交。
2. 后续小修：R2（陈旧模式暴露）、R3（失败 tick 测试）、R8（`dispatch_enabled` 交叉校验）；每项独立验证。
3. `d42030e` 的推送与远程 CI 由所有者/用户授权后执行，本审查不代办。
4. R4 的计划任务 23 小时上限与 Desktop 备用注册重启缺口单独修复并做实机终止/重启演练。
5. 真实原会话派工与 ACK 仍不在本审查范围；任何实机发送都需逐条用户授权，并按交付真值链逐级取证。
