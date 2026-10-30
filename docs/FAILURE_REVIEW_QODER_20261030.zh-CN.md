# Qoder 派工失败复盘与联邦执行升级方案（2026-10-30）

## 结论先行

这次失败不是“模型不会协作”，而是执行链把三个不同事实混成了一个事实：

1. 窗口存在；
2. 输入框里出现了文字；
3. 目标 Agent 已收到、已接单并开始执行。

它们必须是三个独立状态。当前 Qoder 仍在执行用户已经发出的任务，因此本轮不应再发送任何消息；等待执行结束并取得结果后，才允许复用原会话继续派工。

## 事实与失效模式

### 已确认事实

- Qoder 与 Qoder CN 是两个不同身份，不能用进程名子串或模糊标题互相匹配。
- Qoder 当前使用原会话执行长任务；此时窗口仍存在，输入控件也可能仍可见。
- 早期适配器允许“找不到精确输入锚点时退回窗口底部几何点击”，这在界面滚动、DPI、分栏或任务执行状态变化时会把消息写入错误区域。
- 过去的“发送成功”必须以发送按钮动作、输入框清空、事件 ID 出现在同一会话记录、目标回复 ACK 四项证据联合确认；单独 OCR 看到事件 ID 不足以证明发送。

### 根因分类

| 类别 | 根因 | 后果 | 修复方向 |
|---|---|---|---|
| 忙态竞态 | 未在输入前读取生成/工具执行状态 | 覆盖当前任务、排队到错误轮次或打断执行 | 只读 readiness probe；`busy` 时 `DEFERRED` |
| 会话漂移 | 旧会话标题/marker 失效，或 Qoder/Qoder CN 混淆 | 消息进入错误会话 | 进程路径、PID、窗口标题、会话 marker 五级锚定 |
| 控件漂移 | Qoder 输入 placeholder 和发送按钮样式更新 | UIA/OCR 找不到控件，几何兜底误点 | 多 placeholder、严格 lower-band 证据、颜色探针 |
| 假成功 | 输入框有文字就当作已发送 | 产生不可追溯的“已派工” | `INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK` 分段记账 |
| 并发冲突 | CLI/GUI/无头通道同时触达一个会话 | 重复发送、上下文交叉 | 单事件 lease、桌面输入锁、通道互斥 |
| 监控过度 | 以高频截图/点击代替事件驱动观察 | 占用电脑、扰动用户工作 | 低频只读观察；仅在 ready 边沿派发 |
| 交接丢失 | 新会话没有完整快照和原始 event ID | 长任务无法接力 | Workspace 快照、DAG、checkpoint、handoff packet |

## 已落地的本轮改动

### 1. 忙态前置门

`ui_fallback.run()` 现在在任何输入前读取 `activity_state`：

- `busy` → `AGENT_BUSY`，不触碰输入框；
- `unknown` 且 profile 要求严格 ready → `ACTIVITY_UNCONFIRMED`；
- `ready` → 才允许进入会话选择和发送阶段。

这是“等当前 Qoder 指令结束后再推进”的机器可执行版本。

### 2. Qoder 画像更新

Qoder 画像现在支持：

- 多个输入框 placeholder；
- OCR 必读的活动状态；
- 严格 composer hint，禁止无锚点几何猜测；
- 绿色/青绿色发送按钮探针；
- 原会话与模型 marker 继续作为发送前硬门。

Qoder profile 位置：`coordination/ui_profiles/qoder.json`。

### 3. 发送证据保持分层

系统仍严格区分：

```text
WINDOW_FOUND
  → READY_OBSERVED
  → INPUT_VERIFIED
  → SEND_CONTROL_ACTIVATED
  → MESSAGE_VISIBLE
  → AGENT_ACK
  → RESULT_VERIFIED
```

任何中间状态都不能被上报为“已接单”。不确定写入不自动重试；只有确定没有触碰输入的 `DEFERRED` 才能恢复。

### 4. 本地回归证据

本轮针对 UI fallback、Qoder/CodeBuddy 画像、发送安全、原会话回读和 launch-only receipt 运行了 27 个测试，全部通过。真实 Qoder UI 闭环仍需等当前长任务自然结束后再执行，不能用静态测试冒充生产验收。

## 目标架构：自建执行系统 + REA 排障 + 去中心化联邦

```text
                   ┌────────────────────────────┐
                   │ Workspace / Event Journal  │
                   │ SQLite + FTS5 + JSON/DAG   │
                   └──────────────┬─────────────┘
                                  │
          ┌───────────────────────┼──────────────────────┐
          │                       │                      │
   ┌──────▼──────┐        ┌───────▼────────┐      ┌──────▼──────┐
   │ Supervisor   │        │ Agent Adapters │      │ REA/诊断岛  │
   │ lease/DAG    │        │ A2A/MCP/UIA   │      │ 逆向/取证   │
   └──────┬──────┘        └───────┬────────┘      └──────┬──────┘
          │                       │                      │
          └──────────────┬────────┴──────────────┬───────┘
                         │                       │
                  ┌──────▼──────┐        ┌──────▼──────┐
                  │ Desktop     │        │ Headless    │
                  │ Runner/UIA  │        │ CLI/CDP/MCP │
                  └─────────────┘        └─────────────┘
```

### 自建化执行系统

- Supervisor 只负责事件、租约、预算、DAG 和 checkpoint；
- Desktop Runner 在登录用户会话运行，负责真实窗口、原会话和控件证据；
- Headless adapter 只在绑定到同一 session anchor 后启用；
- 所有 transport 共享同一个 `WorkspaceID + event_id + operation_key`，避免重复副作用；
- 本地 SQLite/FTS5 保存事件和经验，Markdown 保存人类可读交接，JSON 保存机器契约。

### REA 离线排障岛

REA 不是直接发送器，而是“证据和故障诊断层”：

- 分析 Electron/CLI/MCP/本地 websocket 的入口；
- 提取 UI 事件、session id、消息队列和错误堆栈；
- 把一次失败归类为窗口、会话、控件、网络、模型或厂商后端问题；
- 生成可复现的 probe 和最小修复建议；
- 诊断结果进入 `command_memory` 和全局策略库，供后续 Agent 复用。

### 去中心化协作

Agent 不是静态“从属节点”，而是可互相唤醒的对等节点；但每个副作用仍由租约和 event ID 约束：

- 任一在线 Agent 可提交派工请求；
- 目标 Agent 可认领、转派、拒绝或接管；
- 目标离线时，备用 Agent 按能力而不是按顺序接力；
- 任何 Agent 都能读取公共情报池，但候选假设仍先隔离探索，防止全队过早趋同。

## 红皇后—鲶鱼—创造性破坏的升级版

### 红皇后：外部变化驱动，而不是永久高频轮询

只在以下事件发生时推进：任务完成、回执到达、额度变化、进程状态边沿变化、文件工件变化、外部回表变化。静默状态不截图、不点击、不生成无意义消息。

### 鲶鱼：有预算的异议与压力

每个任务至少保留一个独立挑战角色；挑战必须绑定证据、时间预算和停止条件。没有新证据的重复质疑不消耗桌面输入额度。

### 创造性破坏：先注册实验，再淘汰方案

候选策略先写入 proposal/experiment；只有通过最小复现实验、独立复核和产物校验，才晋升为默认策略。失败方案进入可检索的负面经验库，阻止团队再次踩坑。

## 接管与自动推进规则

1. `AGENT_BUSY`：保持原 event 不变，进入等待；不得发送第二个事件。
2. `READY_OBSERVED`：复用同一原会话，重新读取快照，才允许发送下一事件。
3. `SEND_UNCERTAIN`：冻结事件，禁止自动换通道重发，先用 REA/回读判断是否已产生副作用。
4. `AGENT_ACK`：写入认领租约和下一阶段依赖，只有依赖满足才派发下一个任务。
5. `TOKEN_EXHAUSTED/PROCESS_EXIT`：从最近 checkpoint 生成 handoff packet，切换备用 Agent；旧 Agent 只读归档。
6. `WINDOW_NOT_UNIQUE/SESSION_NOT_VERIFIED`：阻断并请求重新绑定，绝不猜窗口。

## 下一阶段验收门

### Gate A：Codex → Qoder 原会话真实闭环

- Qoder 当前任务结束；
- 观察到 `ready`，不改变现有会话；
- 新事件输入后，点击真实发送控件；
- 同会话记录出现事件 ID，输入框清空；
- Qoder 回复挑战 ACK；
- 回执和截图/文本证据写入 `coordination/commander/receipts/`。

### Gate B：同一契约扩展到 Qoder CN、WorkBuddy、WorkBuddy AI、CodeBuddy CN

- 每个 Agent 有独立 profile、session anchor 和能力清单；
- 逐个完成一次无副作用的 `inspect → send → ACK`；
- 任一失败只冻结该 Agent，不影响其他 Agent。

### Gate C：无人值守接力

- Supervisor 仅低频读取状态；
- 机器重启后从快照恢复；
- 忙态结束自动触发一次 ready 边沿派发；
- 发送不确定时进入人工可读 dead-letter，不重复副作用；
- 最终产物、checksum、验收证据齐全后才宣告完成。

## 当前诚实状态

当前可以称为：`federated-execution-ready-with-qoder-readiness-gate`。

还不能称为“所有 Agent 已完全无人值守”。真实 Qoder 闭环必须等当前指令结束并取得 ACK 后验收；在此之前，最正确的动作是等待，而不是继续点击。
