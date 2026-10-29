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
