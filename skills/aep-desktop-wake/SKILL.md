---
name: aep-desktop-wake
description: 唤醒队友、投递派工消息并在目标忙碌时安全插话；只写文件也能发起，回执按表判定，不靠模型理解界面。
min_agent_capability: file-only
model_required: false
---

# 唤醒、派工与忙碌态插话

## 何时用

需要把一条带事件 ID 的指令送进另一个 Agent 的**原会话**，或目标正在运行模型时需要紧急插话。

「能力弱的 Agent 不需要会操作别人的界面」：发起方只给出「发起者、目标、任务、指令、证据」，本地中继负责在线校验、投信箱、选适配器、去重和回执。完整规范见 `docs/coordination/ANY_AGENT_CAN_DISPATCH.md`。

## 前置条件

1. 发起者必须有未过期的在线证据；该证据若绑定会话 ID，请求里的 `session_id` 必须匹配。
2. `allow_new_session` 默认 `false`。**禁止**为绕过上下文缺失而新建会话。
3. 截止时间 `reply_due_at` 用带时区的 ISO 时间，且不超过 3 小时。
4. 事件 ID 格式 `DYYYYMMDDTHHMMSSffffffZ-12位小写十六进制`，文件名与 `id` 完全相同。

## 步骤

**路径 A（只会写文件也能做）**：按下面结构写 `coordination/delegation/inbox/<id>.json`：

```json
{
  "id": "D20261105T120000000000Z-123456abcdef",
  "from": "qoder",
  "to": "workbuddyai",
  "task": "T130",
  "instruction": "请独立排查这段编译错误并给出最小实验",
  "evidence": ["K:\\project\\error.txt"],
  "priority": "normal",
  "session_id": "原会话 ID",
  "reply_due_at": "2026-11-05T15:00:00+08:00",
  "allow_new_session": false
}
```

**路径 B（有命令行）**：执行

```
scripts/ask-teammate.ps1 -From qoder -To workbuddyai -Task T130 -Instruction '<指令>' -Evidence '<路径>' -SessionId '<原会话 ID>'
```

然后：

1. 读 `coordination/delegation/receipts/<id>.json`，抄下 `status`。
2. 查「回执判定表」得到结论。**不要**把队列状态当成对方接单。
3. 需要查询状态时用 `aep coord-wake-status --tail 5`，需要核对某条投递用 `aep coord-delivery-status --to <agent> --id <事件ID>`；汇总用 `aep coord-delivery-audit`。
4. 目标忙碌且需要插话时，先确认 profile 里 `interject_after_send` 的值，再按下面的顺序，不自行判断：
   - `false`：先找到 profile 精确声明的控件 → 再写入并发送。
   - `true`（Qoder 0.5.3 国际版/CN 版已实测）：真实输入事件写入 → **强制回车 1~2 次** → 证明消息已发出 → 轮询等待 `插话` Button（`interject_polls`）→ 点击 → 回读。不要求独占窗口。
5. 全程只读回执字段，不根据截图「看起来像」就下结论。完整真值表见 `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`，发送与插话细则见 `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md`。

## 回执判定表

| 字段 | 取值 | 结论 | 下一步 |
|---|---|---|---|
| `status` | `WAKE_QUEUED_UNVERIFIED` | 已排队唤醒，**不算接单** | 等待，继续读回执 |
| `status` | `RELAY_QUEUED_UNVERIFIED` | 已请队友中继，不算接单 | 等待 |
| `status` | `MAILBOX_ONLY` | 只存进信箱，不算接单 | 等待 |
| `status` | `REJECTED` | 验证失败 | 停止，核对在线证据与会话 ID |
| 阶段 | `DEPOSITED` / `WINDOW_FOUND` / `INPUT_SUBMITTED` | 都不是唤醒成功 | 继续 |
| 阶段 | `MESSAGE_VISIBLE` | 消息可见，**不是接单** | 继续等 ACK |
| `task_delivered` | `true` | 同一会话回读到事件 ID，**不是 ACK** | 继续 |
| `agent_accepted` | `true` | 目标明确接单 | 进入执行跟踪 |
| 阶段 | `AGENT_ACK` / `WORKING` / `EVIDENCE_RETURNED` | 进行中 | 继续 |
| 阶段 | `VERIFIED` | 唯一可关单状态 | 结束 |
| `composer_cleared` | `false` | 消息可能还在输入框 | 停止，不得报告已发送 |
| `interject_promoted` | `true` 且 `interruption=false` | 插话提升成功 | 进入回读 |
| `interruption` | `true` | 杀了回合，算**失败** | 停止并报告 |
| `interject_after_send` + `appears_after_send` | 两半都 `true` | 才允许「先发后插」 | 否则用 `false` 路径 |
| 回执 | `EXECUTOR_TOO_LOW` | 已证明什么都没输入 | 唯一允许换表面的情形（提权执行器，不是让客户端降权） |
| `session_fingerprint.continuity().continues` | 不是 `True` | 不是同一段会话 | 禁止换表面，停止 |

## 失败处置

- 队列四态、消息可见、`task_delivered=true` 都不算接单：继续等待，**不得**重复发送。
- `EXECUTOR_TOO_LOW` 是唯一「已证明什么都没输入」的回执；此时允许执行一次换表面（提权执行器），且 `continues is True` 才可换，`None` 按拒绝。它不消耗重试预算。
- 一旦「可能已经发出」，禁止换表面重试——那等于重复投递。用 `session_fingerprint.continuity` 判定，不要凭窗口标题猜。
- 无唯一窗口、会话标签不匹配、输入框未清空、插话控件轮询超时：一律 `DEFERRED` / `CEASED`，不得回车冒充发送，不得点击「停止/取消」当作插话。
- 重试阶梯只允许 `enter-again` → `paste+enter`（先补回车再重贴），且必须重新观察窗口与控件；旧坐标、旧控件索引、旧截图一律失效。
- 适配器超时：状态保持原状态并记 `TRANSPORT_UNAVAILABLE`，不自动重放可能重复的外部动作。
- 多次失败：暂停并通报人类，不新建会话替代原会话。

## 验收门

1. 执行 `aep coord-wake-status --tail 5` 与 `aep coord-delivery-audit --summary-only`，核对本次事件 ID 的回执与判定表一致。
2. 对照 `docs/coordination/AGENT_WAKE_COMMAND_ACCEPTANCE.md` 逐项确认；`task_delivered=false` 或 `agent_accepted=false` 时不得写「已送达」。
3. 忙碌态插话额外核对 `interject_evidence.appears_after_send`、`interject_promoted`、`interruption`、`send_attempts`、`interject_polls` 五个字段，并确认 `composer_cleared=true`、事件 ID 已离开输入框、回复中有 challenge-response `AEP-ACK:<倒序串>`。

## 边界

本机所有 Agent 共享同一 OS 账户与工作区；在线信号与会话 ID 是一致性校验，**不是抵御恶意本地 Agent 的密码学身份认证**。中继最多一跳，不得循环转派，不得自动上传、覆盖文件或取得超出原任务的权限。
