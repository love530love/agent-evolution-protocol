---
name: aep-delivery-preflight
description: 投递前只读预检与投递后回执复算；输出「能不能发、走哪条路、缺什么」，复用 delivery_verdict，绝不打字、不改 commander 活路径。
min_agent_capability: cli
model_required: false
---

# aep-delivery-preflight

在**任何真实桌面输入之前**，给出确定性结论：能不能发、走 `uia` 还是 `ocr`、缺哪几项证据。
全程**只读**：不执行外部适配器、不输入、不聚焦窗口、不写状态、不启动模型或进程。

## 何时用

- 准备调用 `coord-uia deliver` / `coord-commander tick` / `coord-wake --dispatch` 之前；
- 只读观察（`coord-uia observe`）结果看不懂、无法确定该不该发时；
- 想复核一份发送回执里 `task_delivered=true` 到底有没有证据支撑时；
- 新接入一个客户端 profile，要确认它是否具备可投递的观察证据。

**不适用于**：真正发送消息、唤醒/插话、修复窗口状态、注册 OS 任务、
替 commander 改判定、自动验证未复测客户端的插话能力。

## 前置条件

- 有 `coordination/` 运行时目录，磁盘上已有 `ui_profiles/<agent>.json`、
  `uia_receipts/last-<agent>.json`、`desktop_health.json`（缺失会被明确报出，不算通过）。
- 不需要桌面窗口、不需要管理员权限、不需要模型、不需要 LM Studio。
- **UIPI / 执行器完整性级别 ≥ 目标进程** 仍是真实投递前的硬性人工前置，本卡不判定。

## 步骤

1. **只读预检**（默认，零写入）：

   ```text
   python -m agent_evolution_protocol.coordination.delivery_preflight --coord-root coordination
   ```

   或指定客户端：`... --agent qoder qoder_cn`。
   抄回执：`scanned`、`agents[].readiness`、`agents[].route`、
   `agents[].missing`、`agents[].warnings`、`agents[].verdict.action`。

2. **按回执判定表判断**。`missing` 非空就**停止**，不要尝试发送；
   `warnings` 只是提示（如「未登记 client」），不是放行也不是拒绝。

3. **可选：复核一份发送回执**（仍是只读，第二意见）：

   ```text
   python -m agent_evolution_protocol.coordination.delivery_preflight --coord-root coordination --send-receipt coordination/uia_receipts/<id>-<agent>.json
   ```

   看 `postflight.verified` / `missing` / `failed` / `declared_task_delivered`。
   判定复用 `input_routing.delivery_verdict()`，复算用
   `action_verification.from_send_report()` + `verdict()`。
   **它只是第二意见**：`advisory_only=true`，commander 活路径仍读布尔，
   不得据此宣称已经接通复算或已送达。

4. **可选：投递后加一份 UIA 转写第二判定源**（只读，与四项 OCR 检查**并列**）：

   ```text
   aep --coord-root coordination coord-uia capture --agent <name> --event-id <id> --out-file <转写.txt>
   python -m agent_evolution_protocol.coordination.delivery_preflight --coord-root coordination --send-receipt <回执.json> --transcript-file <转写.txt> --event-id <id> --composer-hint "<profile 的 composer_hint>"
   ```

   看 `postflight.transcript_verdict.verified` 与 `postflight.sources_in_conflict`。
   第二判定源**绝不放宽**主判：`postflight.verified` 仍由
   `action_verification` 的四项检查决定，`advisory_only` 恒 true。
   两源不一致时 `conflict_note=OCR_BAND_FALSE_NEGATIVE_SUSPECTED` 只报事实，
   不把主判改成 true，也不宣称送达。没有 `composer_hint` 就报
   `NO_COMPOSER_BOUNDARY`（边界未知，不靠猜行数）。

5. **真实投递由既有通道负责**：`coord-uia compose`（Electron 客户端的真发路径，
   剪贴板真实粘贴 + OCR 回读）/ `coord-uia deliver` / `coord-commander tick`。
   `compose` 与 `deliver` 一样必须带 `--event-id` 且正文包含该 ID。
   本卡不替代、不包装、不重试它们。发完仍按 `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`
   查回执：**只有 `VERIFIED` 能关单**。

## 回执判定表

| 字段 | 结论 |
|---|---|
| `readiness=DEFERRED` / `verdict.action=defer` | 有硬闸门未过，**不要发**；看 `missing` 具名原因 |
| `readiness=READY_UNVERIFIED` / `send-after-verification` | 输入被观察到可定位，但预检从没输入过 ⇒ **诚实答案仍是「发前要验证」**，不是放行 |
| `route=uia` / `ocr` | 由 `ui_profiles/<agent>.json:transport` 推导；`ocr` 客户端无 UIA 树、靠几何选输入框，需额外验证 |
| `missing` 含 `PROFILE_MISSING` / `OBSERVE_MISSING` / `CAPTURE_ONLY_RECEIPT` | 证据缺失；`capture` 回执不能当预检依据 |
| `missing` 含 `TRANSCRIPT_UNREADABLE` / `SESSION_MARKER_NOT_SEEN` | 会话闸门未过 ⇒ 一个字都不写 |
| `missing` 含 `INPUT_NOT_FOUND` / `INPUT_REGION_MISSING` / `ACTIVITY_BUSY` / `ACTIVITY_UNKNOWN` | 输入框或活动态不明 |
| `missing` 含 `WINDOW_ENUMERATION_DEGRADED` | 窗口枚举熔断，先修环境 |
| `warnings` 含 `CLIENT_NOT_REGISTERED` | 仅提示；`commander` 用 `registry.profile` + `discover`，不必然阻塞 |
| `postflight.verified=null` 且 `declared_task_delivered=true` | **布尔不是证据**：回执缺少 4 项检查字段 |
| `postflight.verified=true` | 4 项检查都通过且无反驳；仍只是第二意见 |
| `postflight.verified=false` | 有检查被反驳，看 `failed`；**不会**被第二判定源改写 |
| `postflight.transcript_verdict.verified=true` | 只读 UIA 转写里事件 ID 在会话区、不在 composer 区；仅证明「消息可见」，不是 ACK / 回复 / VERIFIED |
| `postflight.sources_in_conflict=true` | 主判与第二判定源**并列**不一致；`conflict_note` 具名（如 `OCR_BAND_FALSE_NEGATIVE_SUSPECTED`），不得据此宣称已送达 |
| `postflight.transcript_verdict.verified=null` | `NO_TRANSCRIPT` / `NO_EVENT_ID` / `NO_COMPOSER_BOUNDARY`；缺输入就不猜 |
| `DEPOSITED / MESSAGE_VISIBLE / AGENT_ACK` | 都不是关单证据；**只有 `VERIFIED` 能关单** |

## 失败处置

- **禁止**执行 `coord-uia deliver` / `compose` / `--urgent-interject` / `coord-commander tick`、
  以及任何会真打字的适配器；本卡不碰窗口。
- **禁止**把 `readiness=READY_UNVERIFIED` 读成「可以发」；它要求发前验证。
- **禁止**把 `postflight` 的复算当成活路径已接通；`commander` 切换到复算判定
  必须有真机实测，不能靠单测。
- **禁止**用 `transcript_verdict` 改写主判或把 `sources_in_conflict=true` 读成「其实送达了」；
  它是**并列**的第二判定源，不是对四项 OCR 检查的豁免。
- **禁止**给 `codebuddy_cn` / `workbuddy3` / `chatgpt` 直接加 `interject_after_send=true`：
  这些客户端的忙碌插话未复测/未测，等于放行盲发。
- **禁止**对 `CEASED` / `SUBMISSION_UNCONFIRMED` / `SEND_UNCERTAIN` 订单做
  `resume-deferred` / `--retry-deferred` / `reinforce`（重复投递）。
- 失败只允许三选一：**停止 / 延期 / 升级给人类**。

## 验收门

```text
python -m pytest -q tests/test_delivery_preflight.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。真实断口、硬闸门与
分阶段路线见 `docs/DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md`。
