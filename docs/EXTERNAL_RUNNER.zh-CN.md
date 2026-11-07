# 本地执行入口与恢复账本

`coordination.external_runner` 将外部 CLI 计划推进为真实、可恢复的子进程执行。它不是“已派工”或“Agent 已完成”的捷径：退出码 0 只记录 `PROCESS_EXITED`，仍须由原会话消息可见、同事件 ACK、结果回读逐级升级证据。

## 已落地的执行约束

- 参数、UTF-8 stdin、工作目录、有限正数 timeout、输出字节上限都在 attempt 预留前校验；非法 `text`、`timeout=True/NaN/∞` 不会留下半截事件。
- stdout/stderr 用持续 drain 的有界捕获，不用 `communicate()` 全量堆入内存；回执记录已见字节数、截断标志与 `capture_complete`。
- 同事件同请求读取已有结果；同事件不同请求拒绝；半截/损坏 attempt 保持 `UNKNOWN`，绝不自动重放。
- 输入型计划必须显式携带 `provider + target_agent + session_id`。其三元组与 workspace 哈希成跨进程会话锁；同一原会话的第二个事件返回 `SESSION_BUSY`，且不消耗新的 attempt。
- 超时或执行器中断会留下会话级 `UNCERTAIN` 栅栏。后续不同事件必须先依据真实原会话证据执行 `reconcile-session`，而不是重新发送。
- Windows 使用“父进程持有 Job Object → 受信 bootstrap 先加入 Job → 再启动目标”链路；超时后终止整棵 normally-created 子进程树，并记录 `process_tree_stopped_verified`。虚拟环境 redirector 的 breakaway 限制也会写入回执，不能静默夸大隔离保证。
- 非 Windows 使用独立进程组作为超时清理兜底；其 `process_tree_stopped_verified` 保持 `false`，不伪造 Windows Job 级别保证。

```powershell
$env:PYTHONPATH=Join-Path (Get-Location) 'src'
python -m agent_evolution_protocol.coordination.external_runner desktop-read --root coordination/external_runs --workspace . --event-id EVT-DESKTOP-READ-001 --apply
python -m agent_evolution_protocol.coordination.external_runner t3-send --root coordination/external_runs --workspace . --event-id EVT-T3-001 --target-agent <目标Agent> --session-id <既有线程ID> --text-file task.txt --apply
python -m agent_evolution_protocol.coordination.external_runner worker-resume --root coordination/external_runs --workspace . --event-id EVT-WORKER-001 --agent-type qoder --session-id <原生CLI会话ID> --text-file task.txt --apply
python -m agent_evolution_protocol.coordination.external_runner reconcile-session --root coordination/external_runs --workspace . --provider <provider> --target-agent <目标Agent> --session-id <既有线程ID> --evidence "已核对原会话：未发送/已收到/已完成"
```

去掉 `--apply` 只输出计划。本入口不创造原生会话、不代替 AEP 任务认领，也不把桌面聊天 ID 擅自当作 CLI session ID。LobeHub 仅运行本地 hetero，不接服务器。桌面入口当前仅只读枚举；写操作仍需走已验收的 AEP UIA 路径，并把 `INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK` 写回统一证据链。

结果保存在本地 `result.json`；Job bootstrap 的请求、绑定与有界输出回执在同一 `root/jobs/<event-id>/`。会话状态在 `root/session_state/`，锁在 `root/session_locks/`；二者都只用于本地执行面，不替代跨调度器的上层 Workspace lease。结果判读器仍须依据工具活动、目标回复及产物证据升级状态；当前不自动宣称 `AGENT_ACK` 或 `VERIFIED`。
