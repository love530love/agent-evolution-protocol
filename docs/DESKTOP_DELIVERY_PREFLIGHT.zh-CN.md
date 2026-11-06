# 桌面投递的完善：先补确定性预检，再谈切换活路径

记录日期：2026-11-05（v0.6.24）；2026-11-06 补记 v0.6.25 真机实据与并列的第二判定源

## 1. 现状：手脚能发，但没有「发之前能不能发」的答案

只读盘点确认，桌面投递链路本身是通的：
`commander.tick` → `dispatch` → `_dispatch`（`_dispatch_lock` / `_preinput_retryable` /
`_mark_dispatching` / `session_fingerprint.compare` / `_gate_refusal` / `_pick_transport`）
→ `_profiled_deliver` 或 `_uia_deliver` → 回执 → `AGENT_ACK` / `MESSAGE_VISIBLE` 判定。
Qoder 双构建的「粘贴 → 强制回车 → 等待插话控件 → 点击」序列在 11-05 有真机回执。

但两个断口很硬：

1. **投递前没有任何组件输出「能不能发 + 走哪条路 + 缺什么」的确定性结论。**
   `scripts/desktop-preflight.ps1` 只查 client 注册、exe 与窗口数；
   `scripts/probe_uia.ps1` 只答「树可不可读」；`coord-uia observe` 的回执字段齐全
   （`input_found / input_region / activity_state / session_marker_check / drivable`），
   但 `commander._uia_observe()` 是**死代码，src 内零调用方**；
   `dispatch_gate.check` 是纯文本 12 项闸门，不看 UI 观察；
   `input_routing.delivery_verdict()` 本可给出 `send / send-after-verification / defer`，
   却只在 `coord-route` 离线重放里被调用。
2. **投递后复算没有接进活路径。** `action_verification.from_send_report()` /
   `verdict()` / `provider_readback()` 全是「有代码无消费者」；
   `commander._dispatch` 直接读 `task_delivered` 布尔。这正是侧栏编辑器曾误报
   `task_delivered=true`、毁掉 112 行仍缺闭环的根因。

真机证据也很薄：11-05 全部 `uia_receipts` 最高到 `message-visible`，
`agent_accepted` 全为 `false`，无一 `AGENT_REPLY_VERIFIED`；
`last-codebuddy_cn.json` 是 `input_found=false / transcript-unreadable`；
全量 1452+ 个测试无一真机打字，投递路径全靠 mock 与 `.ps1` 源码文本断言。

## 2. 本版落地：只读预检 + 回执第二意见

新增 `coordination/delivery_preflight.py`。**它不改 commander 活路径**，只读磁盘上
已有的四类事实：

| 证据 | 来源 | 用途 |
|---|---|---|
| profile | `ui_profiles/<agent>.json` | `transport`（`uia`/`ocr`）、`session_marker`、`interject_after_send`、`require_activity_ready` |
| client 注册 | `clients/<agent>.json` | 缺失只报 `CLIENT_NOT_REGISTERED` 警告，不硬拒 |
| 观察回执 | `uia_receipts/last-<agent>.json` | `mode`、`session_marker_check`、`input_found`、`input_region`、`activity_state` |
| 窗口枚举健康 | `desktop_health.json` | `circuit != READY` 即硬闸门 |

**复用既有判定，不造第二套**：把观察结果映射成 `locate` 后调用
`input_routing.delivery_verdict(locate=...)`。因为预检从未真正输入过，
`needs_verification` 恒为真，所以即使一切正常，诚实结论也是
`send-after-verification` / `READY_UNVERIFIED`，**不是**「可以发」。

硬闸门（fail-closed，任一命中即 `DEFERRED`）：
`PROFILE_MISSING`、`OBSERVE_MISSING`、`CAPTURE_ONLY_RECEIPT`（capture 回执不能当预检）、
`TRANSCRIPT_UNREADABLE`、`SESSION_MARKER_NOT_SEEN`、`INPUT_NOT_FOUND`、
`INPUT_REGION_MISSING`、`ACTIVITY_UNKNOWN`、`ACTIVITY_BUSY`、
`WINDOW_ENUMERATION_DEGRADED`。

`route` 由 profile 的 `transport` 推导：`uia` → UIA 观察定位；`ocr` → 几何选输入框，
需额外验证（WorkBuddy 系无 UIA 树，只能 OCR）。运行时才能发现的 CDP 路径，
预检**不猜**。

**回执复算是第二意见**：`postflight()` 用
`action_verification.from_send_report()` + `verdict(required=四项检查)` 重算一份发送回执，
返回 `verified`（`True/False/None`）、`missing`、`failed`、`declared_task_delivered`。
关键对照：只有 `task_delivered=true` 而没有任何检查字段时，`verified=null`
（`NO_CHECKS` / `MISSING_CHECK`）——**布尔不是证据**。
回执里 `advisory_only=true` 恒真，并明写 `commander still reads the boolean`。

硬边界：`ui_input_performed=false`、`models_loaded=0`、`processes_started=0`、
`deployment_performed=false`、`system_tasks_registered=false`。源码不出现任何
外部适配器调用或输入 API，并有 AST/文本守线。

## 3. 为什么这一版不直接改 commander

把 `_dispatch` 从「读布尔」改成「读复算判定」看起来只差几行，但：

- 真机上从未有一次 `AGENT_REPLY_VERIFIED`，自动 ACK 回读在真机上没通过；
- 四项检查字段在旧适配器回执里可能缺失，改活路径会让历史订单被判 `unknown`
  而大面积停摆（`action_verification.verdict` 对缺失检查返回 `None`，不猜）；
- 项目一贯规则是「不改别人的验收测试来让自己通过」「安全层不能靠概率/未实测路径替换」。

⇒ 正确顺序是：先用只读预检积累可复算证据 → 真机单条无害挑战串验证四项检查确实齐全
→ 再谈把复算接进 `_gate_refusal` 之后、`_pick_transport` 之前。
**在此之前把活路径切过去，等于把未实测的判定当成已验证。**

## 4. 分阶段路线

| 阶段 | 内容 | 完成判据 |
|---|---|---|
| **P0（本版）** | 只读预检 + 回执第二意见 | 不打字、不改活路径、复用 `delivery_verdict`、复算 `advisory_only` |
| P1 | 真机单条无害挑战串（qoder 已验证通道） | 四项检查字段齐全、`verified=true`、转写回读 + 见证人三条件一致 |
| P2 | 把 `from_send_report/verdict` 接进 `_dispatch` 作为并列字段（不替换布尔） | 真机对照：布尔与复算不一致时必须显式报出，不静默 |
| P3 | 复活 `_uia_observe()`，在输入租约前调用预检 | 每次真发前都有 `DEFERRED` 拦截记录，且不增加重复投递 |
| P4 | 未复测客户端补测（codebuddy_cn / workbuddy3 / chatgpt 忙碌插话） | 每个 profile 有当日真机回执，再决定是否加 `interject_after_send` |

每期固定流程：先写验收测试 → 全量回归 → 真协作区只读预览 → 需要真机时由操作员授权
→ 按「每次更新务必 push 及发版」交付。

## 5. 明确不做（本版）

- 没有执行任何 `coord-uia deliver` / `compose` / `--urgent-interject` / `coord-commander tick`；
- 没有聚焦窗口、打字、点击、粘贴，没有启动 pwsh 适配器或模型；
- **没有改 `commander._dispatch` 的判定**，没有复活 `_uia_observe()` 进活路径；
- 没有自动判定 UIPI / 执行器完整性级别（仍需操作员在真发前确认，执行方 IL 必须 ≥ 目标）；
- 没有给未复测客户端放开 `interject_after_send`，没有注册 OS 任务，没有启动常驻进程；
- 不承诺 7x24 无人值守，不宣称「代替人类工程师」，不追踪积分/额度。

## 6. 验收

- `tests/test_delivery_preflight.py`：完整观察 → `READY_UNVERIFIED`；
  逐项硬闸门具名拒绝；capture 回执不可当预检；OCR 路由与提示；
  client 未登记只警告；预检零写入；
  布尔 `task_delivered=true` 无检查 → `verified=null`；四项齐全 → `true`；
  一项被反驳 → `false`；`advisory_only` 恒真且注明 commander 仍读布尔；
  AST 守线（无 socket/subprocess/模型/`while`）、源码不含外部适配器或输入 API、
  不新增命令面、能力卡与本文档存在。
- 真协作区对 5 个 `ui_profile` 做只读预演结果见 `docs/releases/v0.6.24.md`：
  **不打字、不写任何运行时文件**。

## 7. 2026-11-06 真机实据：布尔的**假阴性**与并列第二判定源

本日 14:44~15:05 做了一次真机强制桌面投递（事件
`20261106T065238959490Z-9fd49a50`，目标 ChatGPT）。它推翻了上一版的一处过粗结论，
也暴露了新断口：

**执行器不是必须提权。** 执行器是 `Medium Mandatory Level`；用
`Get-Process.Path` 可读性判定目标 IL（`Add-Type` 被安全策略拦，别用）：
`Qoder` / `Qoder CN` 主窗口 Path 为空 ⇒ 提权，UIA 树 `control_names_considered=0`
（UIPI 墙成立）；`ChatGPT` / `CodeBuddy CN` / `WorkBuddy` / `WorkBuddyAI` 的 Path
全部可读 ⇒ 普通 IL，Medium 执行器够得着。「客户端一律管理员运行」不是全局事实。

**`coord-uia deliver` 在 Electron 上发不出去。** 回执
`write_method=uia-valuepattern`、`typed_preview` 仍是 placeholder、
`status=input-not-confirmed`、`task_delivered=false`。`ValuePattern.SetValue`
不触发 web app 的 input event；适配器写完立刻复读，发现不含事件 ID 就 `return`，
后面的粘贴补偿根本到不了。这是**安全闸门正确工作**：没按 Enter、没假回执。
真正打通的是适配器 `-Mode compose`（剪贴板真实粘贴 + OCR 回读），此前只有
`ui_fallback.PowerShellUiaDriver.deliver()` 在 `transport=="ocr"` 时才会选它，
CLI 的 `coord-uia` 没有入口。v0.6.25 把 `compose` 加进 `action` choices，
并与 `deliver` 共用 `--event-id` 与「正文必须含事件 ID」的校验。

**`task_delivered` 布尔出现反向错误：假阴性。** compose 回执
`task_delivered=false`、`submitted-unconfirmed`、四项 OCR 检查全 fail，
但 `composer_verified=true` / `composer_cleared=true`；只读
`coord-uia capture` 的权威 UIA 转写从 17341 → 20163 字符，含完整事件 ID
与四个探针，对方已回复「我会按事件 ID 做一次只读审计…」并正在跑 `rg`。
根因是 OCR band 按窗口相对比例切分，而该窗口在多显示器上的矩形是
`[960, 429, 2880, 1659]`，比例失真 ⇒ 消息被判在 composer 带里。
加上 v0.6.24 见过的「`true` 却四项检查全缺」，结论是
**这个布尔两个方向都不可靠**。

**v0.6.25 的解法是补第二判定源，不是修 OCR 公式、更不是放宽契约。**
新增 `coordination/uia_transcript_verdict.py`：从只读 UIA 转写里，以
「最后一行含 profile `composer_hint`」为**并列**边界，要求事件 ID 在边界之上的
会话区、且不在边界之下的 composer 区，可选探针也在会话区；缺 hint 报
`NO_COMPOSER_BOUNDARY`（`verified=None`），绝不靠猜行数。
`delivery_preflight.postflight()` 新增 `--transcript-file` / `--event-id` /
`--probe` / `--composer-hint`，输出 `transcript_verdict`、
`sources_in_conflict` 与 `conflict_note`。

三条硬边界：

1. **主判不变**：`postflight.verified` 仍只由 `action_verification.verdict()`
   和 `REQUIRED_SEND_CHECKS` 四项决定；第二源不参与、不覆盖、豁免不了它。
2. **`advisory_only` 恒 true**，commander 活路径仍读布尔；`_dispatch` 一行没改。
3. **只并列、不裁决**：两源不一致时 `sources_in_conflict=true` 且
   `conflict_note=OCR_BAND_FALSE_NEGATIVE_SUSPECTED`，这是「需要人看」的实据，
   不是「已送达」的结论。UIA 转写只证明**消息可见**，不是 ACK、不是回复、
   更不是 `VERIFIED` 关单。

真机那份回执复算结果：`verified=false`（主判，三项 failed）、
`transcript_verdict.verified=true`、`sources_in_conflict=true`。
这正是本版要保留的诚实形态：**两个源都摆出来，冲突具名，谁也不替谁做决定**。

另外修了一个运行时缺陷：ChatGPT 是 Store 应用，profile 钉的
`OpenAI.Codex_26.1029.7803.0` 被升级到 `26.1103.3517.0`，精确路径比较失败 ⇒
`LAUNCH_FORBIDDEN: no window`。`--allow-unknown-path` 救不了（Path 非空但不等），
必须从运行进程重读 `exe`。该 profile 是 gitignored 运行时数据，已就地修正。

**本版没有**：真打字/聚焦/点击、修 PowerShell 的 `$delivered` 公式、
改 `commander` 判定、给未复测客户端放开插话、注册 OS 任务、常驻进程。
`compose` 只是把**已经存在**的适配器模式从 CLI 暴露出来，不是新手脚。
