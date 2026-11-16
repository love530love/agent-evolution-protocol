# AEP 常驻控制平面与厂商连接器交接（2026-11-16）

> 交接目的：让任何接手本仓库的 Agent 能继续把 AEP 建成可长期值守、可接力的
> 多 Agent 指挥中枢，同时避免把“发现窗口”或“投递到信箱”误报成真实唤醒。
>
> 本文区分：**已经实现且有本地回归的能力**、**已经发现但尚未实机验收的能力**、
> **下一阶段的明确工程工作**。不要把后两类说成完成。

## 0. 当前任务与不可变验收线

AEP 的核心目标不是“生成消息”，而是把任意 Agent 的任务送入指定原会话，并持续
接管因额度、Token、进程、模型或人工切换造成的中断。

真实派工只有完整走完下列链路才能宣告成功：

```text
SESSION_LOCATED
  → WORKSPACE_ATTESTED
  → ORIGINAL_SESSION_ATTESTED
  → INPUT_SUBMITTED
  → SEND_CONFIRMED
  → MESSAGE_VISIBLE
  → AGENT_ACK
  → TASK_RESULT_READBACK
```

下列信号都**不足以**替代后半段验收：订单文件存在、mailbox deposit、计划任务已
注册、程序进程存在、输入框内有文字、发送控件被点击、MCP server 在线、或心跳文件
新鲜。它们只能作为本阶段的证据。

## 1. 当前基线与已实现能力

- 仓库：`K:\PythonProjects5\agent-evolution-protocol`
- 上游：`https://github.com/love530love/agent-evolution-protocol.git`
- 主线：`main`
- 近期相关提交：
  - `4091cdd`：以 `schtasks.exe /Run` 启动提升权限的按需桌面订单；
  - `2a6af26`：修复 `AEP-WAKE-*` 事件被错误解析为裸 `WAKE` 的问题；
  - `1653f98`：非严格 UI 路线先选择原会话、再判定 busy/ready，防止在错误会话上
    因忙碌而阻断。

| 面 | 已落地 | 真实边界 |
| --- | --- | --- |
| 耐久协调 | Core Host、订单、租约、审计、heartbeat、计划任务包装 | Core Host 健康只证明协调循环在运行 |
| 桌面派工 | profiled UIA relay、CDP 受控投递、dispatch gate、receipt 状态 | 尚未替代每个厂商的原会话实机 ACK |
| 原会话互斥 | `session_lease` 按 workspace/provider/target/session 约束 | 会话 identity 必须由真实 connector 证明 |
| 失败语义 | `PROVEN_NOT_SENT` / `UNCERTAIN` / `DEFERRED` 等 | 中断后不可盲目重发，必须 reconcile |
| 本机值守 | `AEP-CoreHost`、`AEP-DesktopHost` 与 resident status | observe-only 不做 UI 写入；任务注册不等于执行成功 |
| MCP 进程治理 | probe Job containment、AEP 自身生命周期账本 | 不得用模糊进程名全机杀掉别的客户端的 MCP |

当前工作树存在用户/其他执行者的未提交修改；接手者必须先检查，不能覆盖、重置或
擅自提交它们：

```text
src/agent_evolution_protocol/coordination/commander_uia.py
src/agent_evolution_protocol/coordination/uia3.py
tests/test_uia3_driver.py
hal-audit.txt
uia_receipts/
```

## 2. 已确认的关键缺口：会话指纹不等于会话证明

Qoder 实机过程暴露了一个根因：旧 profile 可从自身配置生成 marker/fingerprint，
即使当前前台是另一个对话，也可能出现“指纹匹配”。这不是可接受的生产证据。

Qoder 的用户权威路由当前是：

```text
workspace: agent-evolution-protocol
conversation: 请加入项目协作区
```

它被保存在本机忽略目录 `coordination/ui_profiles/qoder.json`，不应提交私有会话资料。
屏幕 OCR 曾把标题识别为“加入项目协作区”，并丢失“请”；这说明全屏 OCR 只可作为候选
发现，不能作为唯一认证。

### 必须落实的 Route Contract

每个厂商/客户端建立独立 profile，最小字段如下：

```json
{
  "provider": "qoder",
  "target_agent": "qoder",
  "workspace_id": "agent-evolution-protocol",
  "conversation_id": "stable-provider-id-if-available",
  "conversation_title": "请加入项目协作区",
  "original_session_proof": {
    "kind": "api|cdp|uia3|visual",
    "calibrated_regions": ["workspace", "header", "composer", "send"],
    "proof_version": 1,
    "captured_at": "RFC3339",
    "strict_required": true
  },
  "send": {"control_identity": "provider-specific"},
  "ack": {"prefix": "AEP-ACK", "readback_rule": "provider-specific"}
}
```

准入规则：

1. 能取得厂商原生 `workspace_id` 和 `conversation_id` 时，优先走原生 API/SDK；
2. 无原生 ID 但有受控 CDP 时，必须把 WebSocket target 与精确页面/会话绑定；
3. UIA3 必须从限定的 workspace/header/selector/composer 区域读出独立证据；
4. 视觉/OCR 只能发现候选窗口或辅助复核；无法得到独立会话证明时输出
   `SESSION_UNVERIFIED`，不得输入或发送。

现有 CLI 已有 `coord-uia calibrate` 和 `uia3` 的严格路线基础。下一位实现者的第一项
工作是为 Qoder 进行**只读、提升权限**的区域校准，保存 proof receipt，并让
`require_strict_session_attestation=true` 只有在 proof 通过后才允许写入。

## 3. 架构决策：引入常驻 Desktop Relay；不把 Tencent Mars 作为 P0 依赖

### 3.1 为什么需要长期连接

弱 Agent 很难自行完成窗口发现、会话切换、UIA/视觉识别和发送后读回。它们应该只创建
标准 AEP order；具备桌面能力的同一用户会话常驻节点负责执行。因此需增加一个长期运行的
`AEP Desktop Relay`：

```text
Weak Agent / A2A caller / MCP caller
          │ durable AEP order + idempotency key
          ▼
AEP Core Host ── lease / DAG / audit / handoff packet
          │
          ▼
AEP Desktop Relay (one Windows interactive-user process)
          │ capability card + heartbeat + reconnect + receipts
          ├── Qoder connector
          ├── Qoder CN connector
          ├── WorkBuddy connector
          ├── WorkBuddy AI connector
          └── CodeBuddy connector
```

Relay 使用 WebSocket 是合适的：低延迟状态推送、断线重连、心跳、订单确认、游标重放都
自然。但 WebSocket **只是运输层**，绝不等于“目标客户端已收到”。最终 delivery evidence
仍由 vendor connector 产生。

### 3.2 为什么不立即引入 Tencent Mars

腾讯 Mars 是面向跨平台、移动端、小数据与网络信令的 C++ 网络基础组件。它可作为未来
多设备/移动弱网 relay 的研究对象，但当前不直接解决 Windows 上的 Qoder/WorkBuddy 原会话
定位与 UI 写入。直接嵌入将带来 C++ 编译、依赖和运维面，却不会提供厂商对话 ID。

**决定：** P0/P1 使用 Python `asyncio` WebSocket relay；协议保持 transport-agnostic。
若未来确有 Android/iOS、多设备弱网与原生 C++ host 的验收需求，再以独立 ADR 评估 Mars。

## 4. 长连接协议与进程治理要求

Relay 是常驻单例，不是“每个订单起一个 MCP/PowerShell/server”。这样可直接避免近期看到的
多宿主重复 stdio MCP、`conhost` 堆积与 CPU 抖动。

### 4.1 最小事件信封（复用现有 AEP dispatch contract，不另造任务语义）

```json
{
  "schema": "aep.desktop-relay-event.v1",
  "event_id": "AEP-WAKE-...",
  "order_id": "...",
  "workspace_id": "...",
  "target_agent": "qoder",
  "kind": "register|heartbeat|order|receipt|resume",
  "sequence": 42,
  "idempotency_key": "...",
  "sent_at": "RFC3339"
}
```

Required relay behaviour:

- 以 `order_id + idempotency_key` 去重；同一订单不因重连重复输入/发送；
- 每个 target route 同一时刻只有一个 session lease；
- 断线后用最后已确认 sequence 恢复，不把断线理解为取消；
- capability card 明示 `session_attestation`, `native_api`, `cdp`, `uia3`,
  `send_confirm`, `ack_readback` 是否可用；
- 订单收下只报 `ORDER_ACCEPTED`，只有 connector receipt 才能改变 delivery state；
- 所有子进程必须登记 PID、父 PID、启动代际、命令来源和 stop receipt；
- 每 profile 最多一个 adapter 实例；出现崩溃采用有界退避，不得无限重拉；
- Relay 退出时关闭自己启动的 child process；绝不按模糊命令行清理第三方宿主进程。

## 5. 厂商 connector 优先级

对于每个客户端均按下面顺序探测和选择；不要把某个厂商的 UI 控件事实泛化给另一个厂商。

| 优先级 | 通道 | 可证明的能力 | 使用条件 |
| --- | --- | --- | --- |
| 1 | 厂商官方 API / SDK / webhook / extension | 原生 conversation ID、直接唤醒与结果回调 | 官方明确支持且凭据/会话绑定通过 |
| 2 | 官方 CLI / ACP / A2A endpoint | 生命周期、任务状态、产物 | 明确支持同一原会话或可创建受控新会话 |
| 3 | 本地 daemon / named pipe / 受控 WebSocket | 原生 session ID 与事件流 | 仅发现并验证接口后启用，不能猜协议 |
| 4 | CDP | 精确 DOM、输入和页面读回 | WebSocket endpoint 必须绑定 exact target ID 和 profile |
| 5 | 严格 UIA3 | UIA 控件 identity、限定区域文本、稳定控件 invoke | 经过 per-provider calibration，原会话证明通过 |
| 6 | 视觉/OCR | 候选发现、图标/区域校验 | 只作辅助；无法独立证明时 fail closed |

“启动程序”也应由 connector 完成：先发现已有受控实例和原会话；只有 profile 明确允许、且
启动后仍能证明指定会话存在时，才启动客户端。**新开空会话不等于唤醒原会话。**

## 6. 接管与额度耗尽

Agent 被额度/积分/Token/模型拒绝中断是正常运行态，不能等待人工逐个催促。

1. Relay 与每个 connector 上报：在线、会话证明、最近进展、配额预警、输入/发送状态；
2. Core Host 识别 `TOKEN_EXHAUSTED`、`QUOTA_EXHAUSTED`、`CREDIT_WARNING`、
   `PROCESS_EXIT`、`MODEL_SWITCH`、`NO_HEARTBEAT`；
3. 先写结构化 handoff packet：只读原始目标、验收标准、DAG、已证实产物、未完成节点、
   不确定投递；
4. 选择能力卡匹配的备用 Agent/Relay；旧 executor 变只读归档；
5. 新 executor 必须重新证明原会话或明确创建“接管会话”，并在 AEP 中记录关联；
6. 对 `SENDING` / `WAITING_REPLY` / `UNCERTAIN` 状态只允许 reconcile，禁止直接重复高风险
   输入，防止双投。

## 7. 接下来应实施的分阶段工作

### P0：Qoder 严格会话校准（最高优先级）

1. 以只读方式获取 Qoder 的 UIA3 tree 与截图；
2. 校准并保存 workspace/header/conversation/composer/send 的 bounded proof regions；
3. 对 `agent-evolution-protocol / 请加入项目协作区` 做独立双证据读取；
4. 证明失败时返回 `SESSION_UNVERIFIED`；
5. 只有用户针对具体消息确认后，才做一次真实 `输入 → 发送 → 可见 → AEP-ACK → 回读`；
6. 将 receipt 和屏幕/结构化 readback 写入协调审计，不把输入成功当 ACK。

### P1：实现本机 Desktop Relay

- 新模块建议：`coordination/desktop_relay.py`；
- 默认仅监听 `127.0.0.1`，支持一次性随机认证令牌；
- 所有状态继续由 coordination JSON/SQLite 投影保存，Relay 不成为第二个权威数据库；
- 提供 `register / capability / heartbeat / resume / receipt`；
- 最小测试：重复 order、断线重连、lease 冲突、adapter 崩溃、child process 清理、未校准路线拒绝；
- 在有完整回归和实际单机 smoke test 前，不注册全天候任务。

### P2：逐厂商 connector contract

顺序：Qoder → Qoder CN → CodeBuddy → WorkBuddy → WorkBuddy AI。每增加一个 provider 都要单独
交付 profile、发现报告、route proof、无害 challenge receipt 和恢复策略，不能批量复制“成功”。

### P3：对外互通

- 将现有 AEP order/status 映射为 A2A AgentCard/Task，A2A 负责异构 Agent 的发现和长任务状态；
- 将 relay 能力暴露为 MCP tools，让无桌面能力的 Agent 可调度受控手脚；
- MCP 使用 stdio 或 Streamable HTTP；WebSocket 保留为 AEP relay 内部实时控制通道；
- 跨机前先做身份、网络边界、重放、断线和恢复测试；不要因为“有 WebSocket”就默认暴露到 LAN/公网。

## 8. 接手者首轮命令

一次只粘贴一行，避免 PowerShell 多行反引号/粘贴顺序问题：

```powershell
Set-Location 'K:\PythonProjects5\agent-evolution-protocol'
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m pytest -q
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' coord-uia observe --agent qoder --process Qoder --title Qoder --timeout 20 --ocr
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\scripts\aep-resident-status.ps1'
```

## 9. 外部参考与定位

| 项目/协议 | 链接 | 在 AEP 的定位 |
| --- | --- | --- |
| Tencent Mars | <https://github.com/Tencent/mars> | 未来移动/弱网原生 relay 的网络研究候选，不是当前桌面会话控制依赖 |
| A2A | <https://github.com/a2aproject/A2A> | Agent 间发现、委派、长任务状态的开放边界 |
| MCP | <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports> | 工具调用边界；Relay 手脚对弱 Agent 的暴露方式 |
| Microsoft UFO | <https://github.com/microsoft/UFO> | Windows UIA/视觉混合执行的设计参考，不直接替换 AEP 订单与审计 |
| UI-TARS Desktop | <https://github.com/bytedance/UI-TARS-desktop> | 视觉 GUI 执行候选，须包入同一 connector/receipt 契约 |
| OpenAdapt | <https://github.com/OpenAdapt/OpenAdapt> | 高频流程录制和回放候选，结果仍要经过 route/session 证明 |

## 10. 成功标准与停止条件

宣布“生产可用”至少应有：

1. Qoder 与一个不同厂商客户端各完成一次原会话完整闭环；
2. 每次都保存 workspace/session proof、输入、发送、消息可见、ACK、结果回读证据；
3. Relay 经断线重连和重复订单演练后不重复发送；
4. 连续 24 小时值守中无重复 MCP/PowerShell child 堆积，并有退出回收证据；
5. 任一执行 Agent 额度耗尽时，备用 Agent 通过 handoff packet 接手至少一个未完成 DAG 节点；
6. 全部状态须在 AEP 审计中可回放，而不是仅存在某个 GUI 或某位 Agent 的自然语言回复里。

在上述证据未齐前，正确状态是“机制已部分实现、真实验收进行中”，而不是“所有 Agent
已经被自动唤醒并完成作战”。
