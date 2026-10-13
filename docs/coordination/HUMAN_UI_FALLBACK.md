# 无人值守时的界面兜底通道

该通道在**已配置且已验收的客户端**上模拟一轮人的操作：找唯一原窗口、必要时打开应用、选择原会话、核对模型标识、定位输入框、发消息、等回复、把回执写回本地。它不是绕过各家安全机制的万能控制权；任何定位或回执证据不足都停止，保留任务等待复核。

## 客户端档案

在 `coordination/ui_profiles/<agent>.json` 建立档案，示例仅展示字段，不能直接用于真实客户端：

```json
{
  "agent": "example_agent",
  "process": "ExampleAgent",
  "title": "Example Agent",
  "exe": "D:\\Apps\\ExampleAgent\\ExampleAgent.exe",
  "session_marker": "原任务会话的唯一标题",
  "model_marker": "当前模型选择器显示的名称",
  "transport": "uia",
  "reply_timeout_seconds": 120
}
```

`transport` 可选 `uia` 或 `ocr`。UIA 仅在唯一可编辑控件明确可见时发令；OCR 必须额外提供屏幕下半部**唯一且完全匹配**的 `composer_hint`，否则不点击。`allow_unknown_path` 默认关闭；只有进程路径因权限不可读且窗口/PID 已通过独立验收时才可开启。档案中的会话和模型标识必须是本机实测值，不能猜。

OCR 客户端如需切回原会话，只在左侧会话栏存在唯一完整匹配标题时点击，随后重新读取页面验证；目标窗口必须完整显示在屏幕内。OCR 兼容助手仅执行屏幕图片识别：本机 PowerShell 7 不能加载 `Windows.Media.Ocr` 类型，故单独使用 Windows PowerShell 5.1，其他调度和界面操作仍由 PowerShell 7 执行。OCR 返回的模型名称只是屏幕文字证据，不能证明它一定来自模型选择器；验收档案时应核对布局与截图，并对模型标识含糊的客户端保持停用。

先用一条无害测试消息验收。直接调用示意：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m agent_evolution_protocol.coordination.ui_fallback --profile 'coordination/ui_profiles/example_agent.json' --message-file 'coordination/test-message.txt' --event-id 'EVT-UI-TEST-1' --receipt 'coordination/ui_receipts/EVT-UI-TEST-1.json'
```

默认不会打开新应用；明确允许启动时加 `--allow-launch`。启动后仍必须重新找到原会话与模型标识，否则停下，不新建聊天。一次事件只发送一次：发送前先落盘 `SEND_ATTEMPTED`，进程中断或发送不确定时，相同回执路径的重试只返回现有状态，不会重复输入。需要人工核对后创建**新的**事件 ID 才能再尝试。

消息要求目标 Agent 回复一个由随机串倒序得到的 `AEP-ACK`。完整回复令牌不出现在发送文本里，因此输入框草稿或自己的发言不能充当接单证据。检测到该令牌，且窗口 PID、会话标识和模型标识均未变化，才写 `AGENT_REPLY_VERIFIED`；回复文本保存为 `reply_excerpt`，供下一轮指挥决策。没有令牌、超时、窗口改变或模型改变都不自动重发。

唤醒守护程序会在档案存在时优先选用此通道；没有档案的客户端继续使用各自已验收适配器或仅投递信箱。所有桌面输入由一把本地锁串行化，防止多个 Agent 同时操作同一键盘/窗口。静默期间没有模型轮询；等待本轮回复时才进行最多 300 秒的有界读回。

## 当前边界

- UIA 会话列表不可见、OCR 输入提示不唯一、模型标识不可见时，不会盲点。
- OCR 是屏幕观察，若系统拒绝把目标窗口置前，通道会失败；不能把屏幕内容当成独立 API 回执。
- `AGENT_REPLY_VERIFIED` 说明对方按挑战格式回复，不证明任务已完成或产物可上传；下一步仍需依据回复与证据分工、实验和审阅。
- 此通道尚需逐个实机验收档案。不要给未验收客户端预先配置猜测的模型名或会话标题。
