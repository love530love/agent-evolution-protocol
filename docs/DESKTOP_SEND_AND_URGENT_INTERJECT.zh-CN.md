# 桌面派工发送与紧急插话规范

本规范固化两条不同的 UI 路径，避免“输入框里看见文字”被误报为“已经发送”：

1. 普通派工：定位原会话和输入框，写入事件 ID，优先按 `Enter` 发送；只有显式配置 `send_mode=button` 时才点击发送按钮。
2. 紧急插话分两类 profile，**顺序相反，不能混用**：
   - `interject_after_send=false`（默认）：先观察由 profile 精确声明的“插话/继续执行并发送”控件，调用该控件后再写入短消息并发送。
   - `interject_after_send=true`（Qoder 0.5.3 国际版与 CN 版均已直接实测）：忙碌时**可以正常发出消息**，插话控件在消息发出之后才出现。序列为：真实输入事件写入 → 强制回车 1~2 次 → 证明消息已发出 → 轮询等待 `插话` Button 出现 → 点击 → 进入模型响应回合。**不需要独占窗口。**

两条路径都必须完成回读。输入框仍然保留事件 ID、窗口没有唯一定位或 ACK 不匹配时，状态只能是未确认/延期，不能报告完成。**看不到插话控件不等于没有插话能力**：对 `interject_after_send=true` 的 profile，控件本来就是发送后才出现，准入只看 `activity.state=busy`，不要求发送前看见控件。

## Profile 字段

```json
{
  "send_mode": "enter",
  "allow_busy_interject": true,
  "interject_after_send": true,
  "interject_hints": ["插话", "继续执行并发送", "Interject"]
}
```

`interject_after_send` 与 `interject_evidence.appears_after_send` 是**两半都要成立**的判定：适配器要实测证明控件在发送后才出现，profile 也要声明该形态；只有一半成立时不得启用「先发后插」路径。`qoder.json` / `qoder_cn.json` 已按实测置为 `true`，`codebuddy_cn.json` 因未复测仍保持严格闸门。

`send_mode` 可取：

- `enter`：聚焦输入框后发送回车。当前默认值，适合 CodeBuddy、Qoder 等将回车绑定为发送的客户端。
- `button`：只使用 UIA/OCR 精确发送按钮或 profile 几何校准，不回车。
- `auto`：为兼容性保留；适配器可以先回车再依据“消息仍停留在输入框”的证据决定是否使用按钮。没有这项证据时不得二次发送。

`interject_hints` 是安全边界，不是模糊搜索词。执行器只接受名称完全匹配、可见且已启用的控件；OCR 回退同样要求唯一精确文本。即使页面同时出现“停止/取消”，也绝不把它们当作插话按钮。

## 状态机

普通派工：

`PREFLIGHT → SESSION_VERIFIED → SEND_ATTEMPTED → WAITING_REPLY → AGENT_REPLY_VERIFIED`

紧急插话：

`PREFLIGHT(busy) → URGENT_INTERJECT_READY → SEND_ATTEMPTED(interject_action) → WAITING_REPLY → AGENT_REPLY_VERIFIED`

`interject_after_send=true`：

`PREFLIGHT(busy) → SEND_ATTEMPTED → MESSAGE_DELIVERED_PROVEN → WAIT_INTERJECT_CONTROL → INTERJECT_PROMOTED → WAITING_REPLY → AGENT_REPLY_VERIFIED`

`WAIT_INTERJECT_CONTROL` 是**轮询**（默认 10 秒），不是看一眼就判失败。重试阶梯固定为「先补回车 `enter-again`，再 `paste+enter`」，不得一上来重贴造成重复投递。

以下状态都表示尚未写入或尚未证明写入，允许 supervisor 在下一次**低频**就绪观察后恢复：

- `AGENT_BUSY`（普通消息）；
- `URGENT_INTERJECT_UNAVAILABLE`（发送前形态下没有唯一、非中断的插话控件；或发送后形态下轮询超时）；
- `ACTIVITY_UNCONFIRMED`、`SESSION_NOT_VERIFIED`、`NO_SAFE_COMPOSER`。

以下状态发生过输入或发送尝试，必须保留原始收据并人工/策略复核，禁止盲目重发：

- `SEND_UNCERTAIN`、`URGENT_INTERJECT_UNCERTAIN`、`REPLY_TIMEOUT_UNCERTAIN`。

## CLI 示例

普通消息（回车优先）：

```powershell
python -m agent_evolution_protocol.coordination.ui_fallback `
  --profile coordination/ui_profiles/qoder.json `
  --message-file coordination/messages/next.txt `
  --event-id EVT-QODER-001 `
  --receipt coordination/receipts/EVT-QODER-001.json
```

紧急插话（只在确认 profile 已配置精确控件名后使用）：

```powershell
python -m agent_evolution_protocol.coordination.ui_fallback `
  --profile coordination/ui_profiles/qoder.json `
  --message-file coordination/messages/urgent.txt `
  --event-id EVT-QODER-URGENT-001 `
  --urgent-interject `
  --receipt coordination/receipts/EVT-QODER-URGENT-001.json
```

联邦 commander：

```powershell
python -m agent_evolution_protocol --root .agent-evolution coord-commander submit `
  --target qoder --task REVIEW --text-file coordination/messages/urgent.txt `
  --priority urgent --urgent-interject
```

`--urgent-interject` 不等于“打断模型”。它只允许在目标报告 `busy` 时进入插话分支；`interject_after_send=true` 的 profile 会先完成发送、再轮询等待精确控件，没有该证据时延期，不会点击停止按钮。

## 验收要点

- `send_method` 必须记录 `enter` 或精确按钮方法；
- `composer_cleared=true` 且事件 ID 不再位于输入框；
- `interruption=false`，并记录 `interject_action`；`interruption`（杀回合，算失败）与 `interject_promoted`（提升成功）是两个不同的回执，绝不合并；
- `interject_evidence.appears_after_send=true` 与 profile `interject_after_send=true` 两半同时成立，才允许先发后插；
- 回执中 `send_attempts` / `interject_polls` / `interject_seen` 必须记录，用于复算「先发后插」是否真的走完；
- 回复中出现 challenge-response `AEP-ACK:<倒序串>`，才升级为 `AGENT_REPLY_VERIFIED`；
- UI 观察必须能证明仍是同一个 PID、原会话和原模型。
