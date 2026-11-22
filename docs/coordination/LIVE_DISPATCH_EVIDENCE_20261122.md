# Qoder 原会话实投证据 · 2026-11-22

## 事件

- 目标：Qoder 国际版，原会话标记 `请加入项目协作区`，逻辑工作区 `agent-evolution-protocol`。
- 消息事件：`20261122T034130140395Z-bef2fe82`。
- Commander 订单：`2026-11-22T0341301714420000-dc761662`。
- 回执：[20261122T034130140395Z-bef2fe82-qoder-profile.json](../../coordination/commander/receipts/20261122T034130140395Z-bef2fe82-qoder-profile.json)。

## 已证实的事实

本次只调用了一次 `coord-send --dispatch`，由该命令对新建订单执行单订单派发；没有运行会遍历历史订单的 `coord-commander tick`，没有重发。

| 验收节点 | 证据 | 结论 |
| --- | --- | --- |
| `SESSION_LOCATED` | Qoder 唯一窗口、精确可执行文件、会话标记、模型标记和唯一输入框均被适配器读到 | 已通过现有 UIA 档案路径 |
| `INPUT_SUBMITTED` | 回执记录 `input_focused=true`、`input_verified=true`、`send_attempts=3` | 已通过 |
| `MESSAGE_VISIBLE` | 回执记录 `task_delivered=true`、`send_report.status=message-visible`，并保存了事件文本见证 | 已通过 |
| `AGENT_ACK` | `agent_accepted=false`、`state=NO_REPLY_WITHIN_TIMEOUT`、无 `reply_excerpt` | 未通过 |
| `TASK_RESULT_READBACK` | 要求的 `QODER_FAIL_CLOSED_REVIEW_20261122.md` 未出现 | 未通过 |

## 限制与后续动作

Qoder 当前使用的是旧式、非严格 UIA 档案。现有会话/模型标记可支持本次消息可见性验证，但不等同于严格 UIA3 有界会话证明；不得将本次结果写作 `STRICT_ORIGINAL_SESSION_ATTESTED`。

同一事件的发送状态已经确定，不能通过重复输入来恢复。后续只可在同一原会话中读取同事件 ACK 或产物；若确认本轮无法继续，须先进行新的严格路线校准和新鲜会话证明，再创建新的事件。

