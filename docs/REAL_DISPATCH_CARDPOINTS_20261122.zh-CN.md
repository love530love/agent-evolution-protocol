# 真实派工卡点与处置（2026-11-22 16:15 CST 实测）

> 本文只记录**真机投递**暴露的卡点和可复算处置。信箱落盘、窗口聚焦、
> `task_delivered=true` 都不是 ACK；只有目标在原会话带事件 ID 的回复才可能构成 ACK。

## 1. 后台 OCR 闸门：observe 0 字 ≠ 会话丢失

实测 workbuddy3 / workbuddy_ai：

```text
coord-uia --agent workbuddy3 observe --ocr      # 后台
  transcript_chars=0, ocr_skipped_reason=BACKGROUND_CAPTURE_UNVERIFIED,
  session_marker_check=transcript-unreadable

coord-uia --agent workbuddy3 capture --out-file ...   # 前台聚焦、只截图不输入
  source=screen-ocr, transcript_chars=1974, session_marker_seen=true
```

根因：`CopyFromScreen` 只能拍到前台桌面。后台 Electron 窗口的 OCR 会拍成别的
应用，所以适配器明确拒绝，这是正确的 fail-closed。**不要放宽闸门**。

正解：前台只读 `capture` 证明会话存在 → 再 `observe --ocr` 拿可投回执。
`CAPTURE_ONLY_RECEIPT` 仍只是“能读”，不是“能投”、更不是送达。

只读诊断：

```python
from agent_evolution_protocol.coordination import delivery_preflight as dp
dp.background_capture_diagnosis(observe_receipt, capture=captured_text)
```

## 2. UNCERTAIN 会话租约：用原会话证据 reconcile，不改文件、不重放

实测 workbuddy3：新 `compose --no-send` 被

```json
{"status":"SESSION_UNCERTAIN","previous_event_id":"20261122T063225623237Z-e80a5332",
 "note":"no UI input was attempted; reconcile the original session first"}
```

拦住。旧回执是 `submitted-unconfirmed`：`composer_cleared=true`、
`probe_in_transcript=false`——按硬规则不能当送达。

前台 OCR 在会话中找到了旧事件 ID、挑战串 `Q-7B2F4D90` 和
“WorkBuddy 已完成 2m50s / 已按派工要求完成回执”，这才构成 reconcile 证据。

正解（只用代码 API，不手改 sidecar）：

```python
from pathlib import Path
from agent_evolution_protocol.coordination import session_lease

root = session_lease.canonical_root(Path("coordination").resolve())
state = Path("coordination/session_leases/session_state/<digest>.json")
binding = json.loads(state.read_text(encoding="utf-8"))["binding"]  # 用存储里的精确对象
with session_lease.acquire(root, binding, timeout=5, allow_active=True) as lease:
    lease.reconcile(evidence="原会话 OCR 证据描述：事件 ID + 挑战串 + 回执")
```

要点：

- `canonical_root(coordination)` 得到 `coordination/session_leases`；传错 root 会
  `NO_SESSION_STATE`。
- **复用存储里的 binding 对象**；用字面量重建可能因反斜杠规范化算出不同 digest。
- reconcile 只解除阻塞，不宣称 ACK，也不重发旧消息。

只读诊断：`dp.session_lease_diagnosis(root, agent, binding=...)`。

## 3. 消息可见 + 客户端“未知错误暂无响应” ≠ 接单

实测 workbuddy3 新派工 `20261122T081450681765Z-0372f1c8`：

```text
status=message-visible, task_delivered=true
probe_in_transcript=true, probe_kind=exact, probe_visible=true
原始抓取: 事件 ID + 挑战串 + “WorkBuddy 已完成 <ls> 抱，发生未知错误暂无响应”
```

消息确实进了会话，但客户端同时报错、没有可读回复 ⇒ 记为 `MESSAGE_VISIBLE`，
不是 `AGENT_ACK`。**不重放、不手工升级**，留给 automation 下轮观察。

只读诊断：

```python
dp.postflight(receipt, transcript=captured_text)["client_response"]
# error_markers / client_error_reported / implies_ack=false / may_advance=false
```

## 4. 身份自会话：workbuddy_ai 就是我当前窗口

前台 OCR 抓到 workbuddy_ai 会话里出现我本轮的分析原话与刚执行的命令，
说明该窗口是我自己的会话。给它“派工”等于自导自演，**排除**。
正确收件名是 `workbuddyai`（见 `docs/AGENT_IDENTITY.zh-CN.md`）。

## 本轮真实状态（不夸大）

| Agent | 本轮事实 | 结论 |
| --- | --- | --- |
| qoder | 真发 `20261122T080133667095Z-9e7bf8bf`，事件 ID+挑战串 `Q7-K4M9P2` 在原会话可见；qoder 回复“收到，先按新指令处理…正在执行中” | 真实接单动作，非最终 ACK |
| workbuddy3 | reconcile 旧租约后真发 `20261122T081450681765Z-0372f1c8`，探针在 transcript 可见；客户端“未知错误暂无响应” | MESSAGE_VISIBLE，非 ACK |
| workbuddy_ai | 前台 OCR 确认是我的自会话 | 排除，不自导自演 |
| qoder_cn | 前台 capture 仍 `uia-tree` 0 字、marker 未见 | 不可投，继续观察 |
| codex / github_copilot / copilot-github | 无本机桌面窗口 | 仅信箱，未跑通 |
