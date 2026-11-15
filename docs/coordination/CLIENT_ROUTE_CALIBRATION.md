# 客户端原会话路线校准

> 相关：[严格原会话证明](../STRICT_ORIGINAL_SESSION_ATTESTATION.zh-CN.md)、
> [UI Profile 的本机边界](UI_PROFILES.zh-CN.md)、
> [人工 UI 兜底通道](HUMAN_UI_FALLBACK.md)。

`coord-uia calibrate` 是严格桌面派工前的**只读校准步骤**。它解决的不是
“能不能点到一个窗口”，而是更窄也更重要的问题：当前客户端的 UIA 树是否同时
唯一暴露了目标原会话、模型标记和消息输入框。

它不输入任务、不按发送、不等待模型回复，也不把“窗口已找到”误写为送达。

## 为什么逐客户端校准

Qoder、Qoder CN、WorkBuddy、WorkBuddy AI、CodeBuddy 的渲染树、会话栏布局、模型
选择器和输入框并不相同；同一客户端重启或升级后也可能改变可访问性树。因此：

- 每个 Agent 有自己的**不可变**校准记录：
  `coordination/route_calibrations/<agent>/<calibration-id>.json`；
- 记录只保存四个归一化区域、哈希和表面摘要，不保存聊天正文或 UI 树；
- 默认的只读重校准只新增一份候选记录，不会覆盖已经被严格 profile 引用的旧记录；
- 校准成功仍不是发送许可。它只允许后续严格 attestation 尝试；
- 重校准引用改变时，旧的 session attestation 的 profile digest 会失效，必须重新
  观察当前会话。

## 首次校准

区域以目标窗口的比例表达：`LEFT,TOP,RIGHT,BOTTOM`，每个值为 `0..1`。先由受控
观察/截图确定区域；不要根据另一客户端、旧分辨率或侧栏猜坐标。

```powershell
aep --coord-root coordination coord-uia calibrate --agent qoder `
  --session-region 0.25,0.00,0.70,0.25 `
  --model-region 0.70,0.00,1.00,0.25 `
  --selector-region 0.00,0.00,0.25,1.00 `
  --composer-region 0.25,0.75,1.00,1.00
```

也可将四个区域放入 JSON 文件：

```json
{
  "session_region": {"left": 0.25, "top": 0, "right": 0.70, "bottom": 0.25},
  "model_region": {"left": 0.70, "top": 0, "right": 1, "bottom": 0.25},
  "selector_region": {"left": 0, "top": 0, "right": 0.25, "bottom": 1},
  "composer_region": {"left": 0.25, "top": 0.75, "right": 1, "bottom": 1}
}
```

```powershell
aep --coord-root coordination coord-uia calibrate --agent qoder `
  --proof-file .\qoder-proof.json
```

默认情况下，原会话已经在活动区域内才会通过。若活动区没有目标会话，而选择器区
只有一个匹配项，才可显式允许一次选择：

```powershell
aep --coord-root coordination coord-uia calibrate --agent qoder `
  --proof-file .\qoder-proof.json --select-original
```

`--select-original` 的点击本身不是成功。点击后 AEP 会再次读取同一窗口的活动区、
模型区和 composer 区；任何不唯一、树重绘或表面变化都会停止为拒绝状态。

## 显式采用

校准成功只会保存证据，不改 `ui_profiles`。确认该客户端当前原会话确实应采用严格
UIA 路线时，才显式附加 `--apply`：

```powershell
aep --coord-root coordination coord-uia calibrate --agent qoder `
  --proof-file .\qoder-proof.json --apply
```

`--apply` 原子地写入 `prefer_uia3`、严格 session attestation 开关、`session_proof`
和一份紧凑的 `route_calibration` 引用；该引用按 ID/digest 精确指向本次不可变
artifact。无效、缺失或不匹配的 artifact 不会更改 profile。

## 结果解释

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `CALIBRATED` | 当前 UIA 表面有唯一会话、模型与 composer | 审核校准，再决定是否 `--apply` |
| `CALIBRATION_NOT_READY` | 当前原会话/模型/表面不能被严格证明 | 调整窗口、恢复无障碍树或使用客户端特化通道；不输入 |
| `CALIBRATION_COMPOSER_NOT_FOUND` | composer 区没有唯一可写控件 | 不猜坐标；重做区域或更换通道 |
| `CALIBRATION_COMPOSER_AMBIGUOUS` | composer 区多于一个候选 | 缩小区域后重新校准 |
| `CALIBRATION_INVALID_PROOF` | 四个区域不合法或 selector 与写区冲突 | 修正 proof JSON，不触及桌面 |

即使 `CALIBRATED`，结果 JSON 中仍固定包含：

```json
{
  "desktop_input_performed": false,
  "message_visible": false,
  "agent_ack": false,
  "task_result_readback": false,
  "delivery_verified": false
}
```

真实派工仍必须分别完成并留下证据：

```text
SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

不得把校准文件、窗口聚焦、按钮点击或 CLI 退出码提升为这五段中的任一后续状态。

## 当前实机状态

截至 2026-11-13，Qoder 最近的 `agent-desktop` capability probe 仍为
`TREE_PARTIAL`：没有可供严格路线使用的当前 composer 证据。因此本功能当前是
**代码与回归测试已实现、目标客户端生产校准 HOLD**，不能据此宣称 Qoder 或其他
客户端已完成自动唤醒、发送或 ACK 闭环。
