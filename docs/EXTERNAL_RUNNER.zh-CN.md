# 本地执行入口与恢复账本

`coordination.external_runner` 将外部 CLI 计划推进为真实、可恢复的子进程执行。它不是“已派工”或“Agent 已完成”的捷径：退出码 0 只记录 `PROCESS_EXITED`，仍须由原会话消息可见、同事件 ACK、结果回读逐级升级证据。

## 已落地的执行约束

- 参数、UTF-8 stdin、工作目录、有限正数 timeout、输出字节上限都在 attempt 预留前校验；非法 `text`、`timeout=True/NaN/∞` 不会留下半截事件。
- stdout/stderr 用持续 drain 的有界捕获，不用 `communicate()` 全量堆入内存；回执记录已见字节数、截断标志与 `capture_complete`。
- 同事件同请求读取已有结果；同事件不同请求拒绝；半截/损坏 attempt 保持 `UNKNOWN`，绝不自动重放。
- 输入型计划必须显式携带 `provider + target_agent + session_id`。其传输 provider、稳定逻辑 `workspace_id` 和**已验证的**原会话 `session_provider`（默认等于传输 provider）构成跨进程会话键；未提供 `workspace_id` 时才回退为解析后的物理工作目录。
- 写路由还必须共用同一个 `coordination/session_leases/` 根目录。收据目录（例如 `coordination/external_runs/`）只存 artifact，不能充当会话锁目录；否则相同哈希仍会落在不同文件夹而双发。
- 输入型 CLI 的 `PROCESS_EXITED` 也只是本地进程证据。只要没有同一原会话的 `MESSAGE_VISIBLE` 证据，lease 都保留 `UNCERTAIN`；超时、执行器中断和“退出但未读回”均须先依据真实原会话证据执行 `reconcile-session`，而不是重新发送。
- Windows 使用“父进程持有 Job Object → 受信 bootstrap 先加入 Job → 再启动目标”链路；超时后终止整棵 normally-created 子进程树，并记录 `process_tree_stopped_verified`。虚拟环境 redirector 的 breakaway 限制也会写入回执，不能静默夸大隔离保证。
- 非 Windows 使用独立进程组作为超时清理兜底；其 `process_tree_stopped_verified` 保持 `false`，不伪造 Windows Job 级别保证。

```powershell
$env:PYTHONPATH=Join-Path (Get-Location) 'src'
python -m agent_evolution_protocol.coordination.external_runner desktop-read --root coordination/external_runs --workspace . --event-id EVT-DESKTOP-READ-001 --apply
python -m agent_evolution_protocol.coordination.external_runner t3-send --root coordination/external_runs --session-lease-root coordination/session_leases --workspace . --workspace-id flaggems-sglang --event-id EVT-T3-001 --target-agent <目标Agent> --session-id <既有线程ID> --text-file task.txt --apply
python -m agent_evolution_protocol.coordination.external_runner worker-resume --root coordination/external_runs --session-lease-root coordination/session_leases --workspace . --workspace-id flaggems-sglang --event-id EVT-WORKER-001 --agent-type qoder --session-id <原生CLI会话ID> --text-file task.txt --apply
python -m agent_evolution_protocol.coordination.external_runner reconcile-session --root coordination/external_runs --session-lease-root coordination/session_leases --workspace . --workspace-id flaggems-sglang --provider <session_provider> --target-agent <目标Agent> --session-id <既有线程ID> --evidence "已核对原会话：未发送/已收到/已完成"
```

去掉 `--apply` 只输出计划。本入口不创造原生会话、不代替 AEP 任务认领，也不把桌面聊天 ID 擅自当作 CLI session ID。LobeHub 仅运行本地 hetero，不接服务器。桌面聊天写操作仍走已验收的 AEP UIA 路径，并把 `INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK` 写回统一证据链。

## 与真实 UIA 原会话共用 lease

`aep coord-uia deliver|compose`、`commander` 的 profiled relay 与本模块复用
`coordination/session_lease.py`。三者的 canonical state/lock 根固定为
`<coord-root>/session_leases/`；external runner 必须传同一路径的
`--session-lease-root`。UIA 的 session key 使用**已验证的** `session_marker`，而不是会随客户端重启变化的 PID；直投命令须带有效 profile marker，否则返回
`SESSION_BINDING_REQUIRED`，不会退化为按进程名盲发。

要互斥的两条路必须声明**完全相同**的四元组，并使用同一 canonical lease 根。不同
transport provider（例如 `uia` 与 `t3code`）默认代表不同的原生会话通道，故不会被猜测为同一个；若适配器已能证明它们指向同一聊天，必须在双方显式声明同一
`session_provider`，并保留 `session_binding_evidence`。例如外部 `t3code` 连接已验证指向 UIA 的 Qoder 原会话时，传：

```powershell
--session-provider uia --session-binding-evidence "已核对 Qoder fingerprint + 原会话 marker"
```

没有该证据的别名会被拒绝，不能靠窗口标题、进程名或“看起来像同一会话”合并。

```powershell
# 只在 profile 已绑定原会话 marker 时允许写入；同一 workspace_id 必须传给所有路由。
aep --coord-root coordination coord-uia deliver --agent qoder `
  --workspace-id flaggems-sglang --event-id EVT-QODER-001 `
  --text "[EVT-QODER-001] 请在当前原会话回复 AEP-ACK:EVT-QODER-001"
```

lease 在真正触碰输入框前写入 `SENDING`。已由 adapter 证明“没有输入/没有点击”的
`NO_WINDOW`、`input-not-confirmed`、`send-control-not-found`、`composer-not-confirmed` 等才会释放为 `PROVEN_NOT_SENT`；adapter 中断、提交未证实、窗口/模型在回读期间变化都会保留 `UNCERTAIN`，必须用原会话可见证据 reconcile。`message-visible` 只会结算为
`MESSAGE_VISIBLE`，不会自动冒充 ACK 或任务完成。

结果保存在本地 `result.json`；Job bootstrap 的请求、绑定与有界输出回执在同一 `root/jobs/<event-id>/`。会话状态在 canonical `session_lease_root/session_state/`，锁在 `session_lease_root/session_locks/`；二者都只用于本地执行面，不替代跨调度器的上层 Workspace lease。读取旧版 artifact root 或旧版 `workspace` key 的 `UNCERTAIN` sidecar 仍会阻塞升级后的写入，直到显式 reconcile，避免升级时误放行重复发送。结果判读器仍须依据工具活动、目标回复及产物证据升级状态；当前不自动宣称 `AGENT_ACK` 或 `VERIFIED`。
