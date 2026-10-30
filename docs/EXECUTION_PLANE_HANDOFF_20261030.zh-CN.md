# 执行平面升级交接文档（2026-10-30）

## 0. 交接目的

本文件用于把当前 AEP（Agent Evolution Protocol）执行平面升级工作交给新的 Agent、评审 Agent 或接替 Agent。目标不是继续堆积“能调用的工具”，而是把异构工具变成可验证、可接管、可恢复的生产执行能力。

接班 Agent 必须先读本文件，再读：

1. `README.md`
2. `docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md`
3. `docs/AGENT_HANDOFF_AND_ACCEPTANCE.zh-CN.md`
4. `docs/EXECUTION_PLANE_UPGRADE_PLAN.zh-CN.md`
5. `examples/execution-provider-catalog.json`

## 1. 当前真实状态

### 1.1 已经落地

- 工作区、事件、信箱、租约、身份锚定、快照和 ACK 基础协议已存在。
- Qoder 国际版与 Qoder CN 已分为两个独立身份、PID、窗口标题和可执行路径。
- 桌面 MCP 候选已登记并完成工具发现；这只证明 `probe/tools-list`，不证明可以操纵原会话。
- 新增 `coordination/execution_plane.py`，统一执行证据和生产路由门槛。
- 新增 `tests/test_execution_plane.py`，覆盖输入未确认、ACK 前置证据和候选 Provider 禁止生产派工。
- Qoder UI 配置已恢复活动就绪门，防止模型忙碌或状态不明时点击发送。
- 全量回归测试：`477 passed`。

### 1.2 尚未完成，不能宣称完成

- Qoder ACP/SDK/本地 IPC 真实派工适配器尚未通过原会话验收。
- Browser Use/Playwright/CDP 尚未完成真实 target 绑定和同标签回读验收。
- UI-TARS/UFO2/Agent S2 尚未形成生产级“截图—动作—截图—ACK”适配器。
- Open Interpreter/OpenHands 尚未完整接入 Workspace DAG、租约和 artifact manifest。
- OpenAdapt 仅能在某一流程先通过真实验收后再生成回放，当前没有可直接信任的回放包。
- 第三方候选目录中的 Provider 全部仍是 `candidate`，不得自动进入生产派工。
- `coordination/ui_profiles/` 与 PID/窗口锚点是本机运行态，当前按 `.gitignore` 不入库；远程复现需要模板化安装器和重新探测步骤。

## 2. 本次 Qoder 失败的可核验证据

目标是 Qoder 国际版，不是 Qoder CN：

```text
国际版进程：D:\Programs\Qoder\.qoder-versions\0.4.11\Qoder.exe
窗口标题：Qoder
UIA 观察：window-observed
输入框：input_found=false
输入候选数：0
活动状态：unknown
task_delivered：false
```

CUA/桌面输入尝试没有产生可观测的页面变化，因此只能归类为：

```text
INPUT_CHANNEL_UNAVAILABLE
```

不能归类为“已发送”，也不能直接重试同一高风险事件。下一步应先探测 ACP/SDK/命名管道/WebSocket/CDP；只有确认通道和会话 ID 后，才能重新生成新事件。

## 3. 统一成功定义

任何 Agent、MCP、Skill 或 GUI 适配器都必须按以下顺序提供证据：

```text
DISCOVERED
  -> PROBED
  -> SESSION_ACCEPTED
  -> INPUT_SUBMITTED
  -> MESSAGE_VISIBLE
  -> AGENT_ACK
  -> TASK_TESTED
```

含义：

- `DISCOVERED`：发现仓库、进程、端口或工具入口。
- `PROBED`：版本、依赖、工具列表和健康检查成功。
- `SESSION_ACCEPTED`：唯一绑定原窗口/标签/设备/原生 conversation ID。
- `INPUT_SUBMITTED`：输入通道接受了内容；不代表发送。
- `MESSAGE_VISIBLE`：同一会话记录中能读到本次 `event_id`。
- `AGENT_ACK`：目标 Agent 按事件 ID 回执并说明下一步。
- `TASK_TESTED`：完成一次无害但真实的端到端任务，并保存回读证据。

最低规则：没有 `MESSAGE_VISIBLE + AGENT_ACK`，不得报告派工成功；没有 `TASK_TESTED`，Provider 不得进入默认生产路由。

## 4. 目前的能力分层

| 层 | 候选能力 | 当前定位 | 接班 Agent 的工作 |
|---|---|---|---|
| 浏览器 | Browser Use、Playwright、Skyvern、LaVague | Browser Connector 候选 | 绑定 target、读回同标签、验证事件链 |
| Windows 桌面 | UI-TARS、UFO2、Agent S2、Self-Operating Computer | 视觉备用层候选 | 截图、动作、回读和输入租约 |
| 终端/文件 | Open Interpreter | 本地执行候选 | 命令、退出码、diff、checksum、恢复 |
| 长程工程 | OpenHands | 工程 Agent 候选 | checkpoint、DAG、handoff、测试产物 |
| 流程回放 | OpenAdapt | 真实验收后的确定性回放 | 录制来源、窗口锚点、异常回退 |
| 移动端 | Appium MCP | 移动设备候选 | device/session/app/report 证据 |
| 逆向取证 | REA | 诊断和归因工具 | 目标身份、探针记录、复现脚本、哈希 |

REA 是诊断手段，不是派工发送器；视觉 Agent 是输入备用层，不是 ACK 证明器；MCP `tools/list` 是能力发现，不是任务完成证明。

## 5. 分工建议

### R0：Qoder 协议通道组

任务：探测 ACP/SDK daemon、命名管道、WebSocket、本地端口和原生 conversation ID；禁止一开始继续坐标点击。

交付：

- `qoder-probe` 只读报告；
- 协议入口和版本证据；
- 可读取原会话的最小 `observe/readback`；
- 失败分类，不发送副作用消息。

验收：新鲜 Qoder 进程、新鲜事件、新鲜工作区下，拿到同一会话 ID 和无害 ACK。

### R0：浏览器通道组

任务：Browser Use + Playwright/CDP 的 target 绑定和同标签回读。

交付：

- `observe_target()`；
- `focus_target()`；
- `compose/send/readback()`；
- 登录态或标签变化时的明确 `SESSION_BINDING_REQUIRED`。

### R0：执行平面一致性组

任务：为所有 Provider 建立同一套 conformance harness。

交付：

- synthetic task；
- `probe/observe/focus/compose/send/readback` 六个动作；
- 正常、无输入、输入未发送、消息可见但无 ACK、超时、重启六类负路径；
- 自动生成 `TASK_TESTED` 或失败证据。

### R1：终端与工程组

任务：接入 Open Interpreter/OpenHands，统一 Workspace lease、命令转录、工件清单和断点接力。

交付：

- 命令和退出码；
- 工作区 diff；
- 工件 checksum；
- 可恢复 checkpoint；
- 不把“模型说已完成”当成完成。

### R1：视觉与回放组

任务：接入 UI-TARS/UFO2/Agent S2，成功后沉淀 OpenAdapt 回放。

交付：

- 目标窗口指纹；
- 截图前后对照；
- 动作序列；
- 输入租约；
- 异常回退和停止条件。

### R2：审计与文档组

任务：维护候选目录、许可证、依赖、上游致谢、证据矩阵和发布说明。

交付：

- 每个 Provider 的 provenance；
- 许可证和分发边界；
- `candidate/probed/session-accepted/task-tested/retired` 状态变更；
- 不把本机隐私、token、会话全文和 PID 推入公开仓库。

## 6. 接班 Agent 的标准操作

### 第一步：读取和确认

```powershell
Set-Location K:\PythonProjects5\agent-evolution-protocol
Get-Content README.md -Raw
Get-Content docs\EXECUTION_PLANE_HANDOFF_20261030.zh-CN.md -Raw
aep --coord-root coordination coord-onboarding --agent <AGENT-ID>
aep --coord-root coordination coord-digest --agent <AGENT-ID>
aep --coord-root coordination coord-status
git status --short
```

### 第二步：声明边界

接班 Agent 必须通报：

```text
身份：<稳定 Agent ID>
工作区：<WorkspaceID/路径>
已读状态源：<文件和命令>
当前认领：<任务 ID 或 NONE>
能力：<能做什么>
限制：<不能做什么>
下一步：<单个 bounded action>
```

### 第三步：认领再修改

先检查已有 owner、lease、checkpoint 和旧事件；任务已认领时只能补充证据或提出协作请求。没有 session ID 时写 `SESSION_BINDING_REQUIRED`，不得新建会话绕过上下文。

### 第四步：回写证据

每个阶段必须写入：

- 事件 ID；
- 输入/输出摘要；
- 失败分类；
- 产物路径和 checksum；
- 下一步和停止条件；
- 是否已经真实回读或只完成计划。

## 7. 失败重试规则

| 状态 | 允许动作 |
|---|---|
| `NO_SESSION` | 重新探测并请求绑定 |
| `INPUT_CHANNEL_UNAVAILABLE` | 切换 ACP/CDP/UIA/视觉通道，冻结原事件 |
| `MESSAGE_SEND_UNCONFIRMED` | 只读检查原会话，不直接重发 |
| `MESSAGE_VISIBLE` 无 ACK | 等待、读取信箱或请求原 Agent 回执 |
| `UNKNOWN` | 交给诊断/仲裁 Agent，不自动重放高风险动作 |
| `AGENT_ACK` | 进入任务执行或下一阶段 |

重试必须生成新 attempt ID，并引用旧事件；不能复用旧坐标、旧控件索引或旧 ACK。

## 8. 红皇后—鲶鱼—创造性破坏在执行平面的落地

- 红皇后：持续寻找新的协议通道、浏览器后端、视觉模型和 MCP Provider。
- 鲶鱼：对每个“已成功”结论提出最便宜的反证测试，例如新鲜进程、换会话、重启后恢复。
- 创造性破坏：连续无法通过 `MESSAGE_VISIBLE + AGENT_ACK` 的 Provider 降级或退役，不因工具名气或多数意见保留生产资格。

## 9. 交接完成判据

接班 Agent 只有同时满足以下条件，才能回复“本阶段完成”：

1. 修改已写入正确工作区；
2. 相关测试通过；
3. 产物路径和哈希已记录；
4. 对真实宿主的结论明确标为 `TASK_TESTED`、`NOT_VERIFIED` 或 `BLOCKED`；
5. 下一位接班者可以仅凭本文件、digest 和 checkpoint 继续；
6. 没有将假设、计划或截图冒充为 Agent ACK。
