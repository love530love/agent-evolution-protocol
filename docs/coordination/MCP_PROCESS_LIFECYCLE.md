# MCP 进程生命周期与 Windows 控制面

## 结论边界

Windows 上的 stdio MCP 是 **一个宿主客户端的一条 stdin/stdout 管道对应一个服务器实例**。因此 Qoder、Qoder CN、WorkBuddy、WorkBuddy AI、Codex 同时各有一份 `windows_mcp serve`，并不自动等于泄漏；不能按 `python.exe`、`node.exe`、`conhost.exe` 或命令行文本做全机批量终止。

AEP 的 `coord-agent-mcp` 只渲染计划/配置，不会写入其他客户端配置文件，也不会启动其服务器。另一方面，`aep coord-desktop probe` 的 provider profile 可以临时启动 MCP（例如 CursorTouch `windows_mcp serve --transport stdio`）用于只读工具发现。旧实现若在外层超时，只能可靠结束 worker，自身的 stdio 后代可能在 Windows 留存；这一条属于 AEP 已修复的放大路径。

## 已落地的约束

1. **探测树隔离**：Windows 的 desktop MCP probe 先创建随机 `AEPProbe-*` Job Object；worker 在启动 MCP 前加入该 Job。超时或退出时仅终止这一次 probe 的已拥有进程树，并在 receipt 中写入 `process_tree_stopped_verified`。它不会触碰任意既有 Qoder/WorkBuddy/Codex MCP。
2. **AEP MCP 账本**：`aep-mcp-server` 启动后建立 `coordination/mcp_instances/<run-id>.json`，周期性心跳，并在退出时标记 `EXITED`。记录只保存 PID、父 PID、Agent、工作区、传输、时间和命令摘要；不保存命令行、token、环境变量或会话正文。
3. **PID 重用防护**：记录以随机 run ID，而非 PID 命名；同一 PID 的两代实例不会覆盖彼此。`coord-mcp-lifecycle prune` 只在保留期后删除 AEP 自己的 `EXITED`/`DEAD_PID` **记录文件**，`processes_terminated` 恒为 0。
4. **单实例常驻控制面**：调度循环迁到 `agent_evolution_protocol.coordination.supervisor`，使用协调根目录独占锁。`scripts/aep-supervisor.ps1` 仅为兼容包装，不再每 30 秒从 PATH 拉起新的 `aep` 子进程。计划任务直接运行项目 Python，并设 `IgnoreNew`。
5. **Watcher 防并发**：`campaign-watch.ps1` 与 `campaign-control.ps1` 分别拥有全局 mutex；控制器等待 watcher 的 ReadyFile 后才写 PID 状态，避免并发启动把状态文件指向已经退出的副本。

## 运维命令

```powershell
# 仅查看 AEP 自己的 MCP 账本；不会扫描或杀死全机进程
aep coord-mcp-lifecycle status

# 只删除保留期已过的 AEP 退出/死 PID 账本文件；仍不会杀进程
aep coord-mcp-lifecycle prune --retention-seconds 86400

# 运行一次受控、只读的工具发现。超时会回收本次 probe 的 Job 子树。
aep coord-desktop probe --provider cursortouch --timeout 20

# 用 Python 单实例监督器做一次可审计 smoke test
pwsh -NoProfile -File .\scripts\aep-supervisor.ps1 -MaxTicks 1 -NoPerceive
```

进程清理的生产准则是：先以 **可验证的根 PID、创建时间、父子树、命令摘要、CPU/内存证据** 划定某一代；仅终止该代自己的根与后代；重采样确认。对“看起来像 MCP”的全机进程名匹配不是 AEP 的清理机制。

## Python 与 Rust 的职责

Python 是当前调度、心跳、DAG、账本和 Job 生命周期的主实现：依赖少，现有协议/测试可直接复用。PowerShell 保留为 Windows 计划任务、UAC、UIA 启动的薄包装，不再承担长期业务循环。若未来需要更强的 Windows 原生服务能力，可增加一个小型 Rust `aep-host`（Job Object、命名管道、服务恢复），通过稳定 JSON/MCP 接口与 Python 核心协作；不进行高风险的整核重写。

## 验收边界

`process_tree_stopped_verified=true` 只证明某次 **probe 进程树** 已回收；它不是原会话消息已发出、更不是 Agent ACK。真实协作仍必须完成：`SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK`。

## 心跳时间异常

`HEARTBEAT_FUTURE` 表示 PID 探测为存活，但记录的心跳时间晚于巡检时间。
应核对系统时钟及进程归属。生命周期清理会保留此状态的记录，不会终止进程。
