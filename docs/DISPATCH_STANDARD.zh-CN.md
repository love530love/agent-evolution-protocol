# AEP 派工标准 v1

## 目的

派工不是“给 Agent 发一段话”，而是创建一份可验证、可回放、可接管的 `aep-dispatch-envelope-v1`。任何桌面、MCP、CDP、A2A、ClawChat 或本地 CLI 适配器都必须保留这份契约的语义。

机器校验器：

```python
from agent_evolution_protocol.coordination import dispatch_contract
dispatch_contract.validate(envelope, strict=True)
```

Schema：`schemas/dispatch-envelope-v1.schema.json`。

## 必填字段

| 字段 | 含义 | 生产要求 |
|---|---|---|
| `order_id` | 一次派工订单 | 不复用 |
| `event_id` | 端到端幂等键 | 必须出现在 instruction 和所有回执 |
| `workspace_id` | 工作区水印 | 严格模式不可省略 |
| `sender` / `target` | 发起者 / 目标 Agent | 使用稳定注册名，不用显示昵称 |
| `task` | 任务键 | 对应任务板/DAG 节点 |
| `instruction` | 给 Agent 的实际指令 | 必须带 event_id、目标和下一步 |
| `session_policy` | 会话选择规则 | 默认 sticky，禁止偷偷新建会话 |
| `preconditions` | 发送前置条件 | 例如会话锚定、模型一致、目标 ready |
| `acceptance` | 完成标准 | 至少包含消息可见、Agent ACK 或业务结果 |
| `evidence_required` | 必须留下的证据 | 不能只写“已完成” |
| `retry_policy` | 重试边界 | 仅允许确认未触碰输入的 pre-input 重试 |
| `stop_conditions` | 何时冻结 | 忙态、会话漂移、发送不确定都必须停 |

## 会话策略

### `sticky`（默认）

继续原会话、原工作区和原模型。`allow_new_session=false`。如果原会话不可验证，返回 `DEFERRED`，而不是新建窗口。

### `anchor-required`

必须拥有 session/conversation ID、PID/窗口锚点、浏览器 target 或其他稳定 anchor。没有 anchor 不得发送。

### `new-session`

只有在明确授权时使用；必须同时提供 `handoff_packet_ref`，并且新会话必须先读取快照、确认工作区和回复接管 ACK。

## 标准状态机

```text
DEPOSITED
  → SESSION_LOCATED
  → READY_OBSERVED
  → INPUT_VERIFIED
  → SEND_CONTROL_ACTIVATED
  → MESSAGE_VISIBLE
  → AGENT_ACK
  → RESULT_VERIFIED
```

以下事实不可互相替代：

- 窗口存在 ≠ 原会话已定位；
- 输入框出现文字 ≠ 已发送；
- 消息可见 ≠ Agent 已接单；
- Agent ACK ≠ 业务结果已验收。

## 严格停止规则

遇到以下任一情况，状态必须是 `DEFERRED` 或 `CEASED`，不得继续点击：

- 目标 Agent 正在执行工具或生成回复；
- 多个同名窗口且没有 anchor；
- session/model/workspace marker 不一致；
- 输入框、发送按钮或会话切换无法独立证明；
- 发送动作发生但回读不确定；
- 外部弹层、登录、验证码或权限对话框覆盖输入区；
- 同一 event_id 已经有活动发送租约。

## ACK 规范

目标 Agent 的 ACK 必须：

1. 来自目标 Agent 自己的 assistant/incoming 事件；
2. 出现在同一 session anchor；
3. 包含原始 `event_id`；
4. 明确说明 `ACK`、当前判断和下一步；
5. 不得用自己发出的文本、窗口聚焦、文件写入或协议层 200/201 响应冒充 ACK。

## 重试与接管

- `AGENT_BUSY` / `ACTIVITY_UNCONFIRMED`：原 event 保持不变，进入 `DEFERRED`；Supervisor 只读探测 ready 边沿。
- `SEND_UNCERTAIN`：冻结，不自动换通道重复发送；先用回读或 REA 诊断是否产生副作用。
- `TOKEN_EXHAUSTED` / `PROCESS_EXIT`：生成 handoff packet，备用 Agent 接管；旧 Agent 只读归档。
- `WINDOW_NOT_UNIQUE` / `SESSION_NOT_VERIFIED`：重新绑定 anchor，禁止猜测。

## 与红皇后—鲶鱼—创造性破坏的关系

- 红皇后：只响应状态边沿、结果回表和外部变化，不制造无意义轮询；
- 鲶鱼：挑战派工假设和证据链，但必须给出可复现实验和预算；
- 创造性破坏：失败策略写入负面经验库，只有通过独立验证的策略才进入默认路由。

## 给新 Agent 的最短接入指令

```text
先读 templates/AGENT_OPERATOR_CONTRACT.zh-CN.md 和 docs/DISPATCH_STANDARD.zh-CN.md。
复用当前 WorkspaceID 和原会话；先回报 READY/DEFERRED，再认领任务。
所有派工必须携带 event_id、前置条件、验收标准、证据要求和停止条件。
未看到同会话 ACK 前，不得宣称已接单或继续派发下一阶段。
```
