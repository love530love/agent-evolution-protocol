# 生产桌面唤醒部署

桌面发送必须经过四个证据门槛：客户端注册、窗口/PID 锚定、原会话定位、发送后回读。输入框出现文字不等于已发送。

## 初始化

```powershell
pwsh -File scripts/register-qoder-cn.ps1 -CoordRoot K:\PythonProjects5\agent-evolution-protocol\coordination -Pid <当前Qoder-CN-PID>
```

多个同名窗口时必须提供当前 PID；没有唯一窗口或会话标签时，系统保持 `DEFERRED`，不会盲点输入。

## 生产验收状态

- `input_verified=true`：只证明文本进入输入框。
- `send_method=uia-button`：证明点击了发送按钮。
- `composer_cleared=true`：证明输入框已清空。
- `status=message-visible`：证明消息出现在目标会话记录。
- `AGENT_ACK`：还需要目标 Agent 按事件 ID 回执，不能由桌面截图代替。

任何缺失都会停止并保留事件 ID，便于显式恢复，不自动重复投递。

上线前可运行预检：

```powershell
pwsh -File scripts/desktop-preflight.ps1 `
  -CoordRoot K:\PythonProjects5\agent-evolution-protocol\coordination `
  -Agent qoder_cn -RequireAnchor
```

预检失败意味着当前窗口或锚点不满足无人值守条件，应先重新注册/锚定，不应直接重试投递。

## 严格原会话模式（v0.6.33，可选）

当 profile 显式设置 `require_strict_session_attestation=true` 时，以上预检仍不够：发送前还必须在同一
session lease 与 desktop lock 内，完成 UIA3 受限区域的原会话证明。配置需要额外的
`session_region`、`model_region`、`selector_region`、`composer_region`；同时要求已有匹配 fingerprint、
可信 runtime id，并在 composer 获得焦点后重新核对活动会话。UIA3 不能继续时会拒绝，而不会转 OCR/PowerShell
盲发。

严格证明只是输入许可，**不是** `message-visible`、ACK 或结果。未完成以下全部链路时不得关单：

```text
INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

标定、拒绝原因与回滚边界见
[`STRICT_ORIGINAL_SESSION_ATTESTATION.zh-CN.md`](STRICT_ORIGINAL_SESSION_ATTESTATION.zh-CN.md)。
