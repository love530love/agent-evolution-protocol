# Desktop-Primary / Headless-Augmentation 验收清单

这份清单是实战验收入口。它不把“进程已启动”“MCP tools/list 成功”或“输入框出现文字”
当作 Agent 已接单。

## A. 启动验收

- [ ] Agent profile 有真实 exe 路径和用户 Session 信息。
- [ ] Supervisor 能从工作区快照恢复待启动 Agent。
- [ ] Desktop Runner 在交互式用户 Session 中启动程序。
- [ ] 记录 PID、进程启动时间、exe 哈希、用户 SID、Windows Session ID、窗口句柄。
- [ ] 有明确的 READY 或 DESKTOP_SESSION_UNAVAILABLE 状态。

## B. 会话定位验收

- [ ] 原生 conversation/session ID 可读取时优先使用。
- [ ] 浏览器目标使用 CDP target ID、URL、浏览器 PID 绑定。
- [ ] GUI 目标使用 UIA AutomationId/RuntimeId 和进程身份。
- [ ] 两个同名窗口时没有 anchor 就返回 `SESSION_BINDING_REQUIRED`。
- [ ] 进程重启后旧 PID 不被复用，必须重建 anchor。

## C. 派工验收

- [ ] 先取得 Workspace/Session lease。
- [ ] 事件包含 Workspace ID 和唯一 event ID。
- [ ] 选择已验收的无头通道，失败再进入桌面通道。
- [ ] 桌面通道失败时不会把输入结果当作消息发送成功。
- [ ] UNKNOWN 结果先读回，不能直接更换通道重复发送。

## D. 回执验收

- [ ] `INPUT_SUBMITTED` 有输入证据。
- [ ] `MESSAGE_VISIBLE` 有会话内消息可见证据。
- [ ] `AGENT_ACK` 必须由目标 Agent 在同一事件 ID 上返回。
- [ ] `RESULT_READBACK` 有任务状态、产物或下一步证据。
- [ ] 每一步都写入持久化 receipt，可跨进程/重启读取。

## E. 无人值守验收

- [ ] 用户已登录时，Supervisor + Desktop Runner 连续运行。
- [ ] 锁屏/注销时 GUI 状态变为 `DESKTOP_SESSION_UNAVAILABLE`，不能伪造完成。
- [ ] 无头 API/MCP/CDP 可用时，任务可继续推进。
- [ ] 进程崩溃后可从 Workspace 快照接力。
- [ ] 任务停止、接力、恢复均有事件和证据。

## 当前测试边界

`tests/test_desktop_primary_headless.py` 只验证状态门、身份绑定和持久配置，
不会操作真实桌面。真实 Agent 验收必须另行记录窗口、事件、消息、ACK、产物和恢复证据。
