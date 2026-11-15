# AEP v0.6.35 收尾交接（2026-11-15）

## 当前提交目标

- 仓库：`https://github.com/love530love/agent-evolution-protocol.git`
- 分支：`main`
- 版本：`0.6.35`
- 本地全量验证：`1744 passed, 3 skipped, 1018 subtests passed in 176.53s`

本交接以“可复现证据”为准。下面的“已实现”不等同于任何外部 Agent
已经接令或完成任务。

## 本版完成项

| 面 | 已落地 | 验收证据 |
| --- | --- | --- |
| 原会话定位 | `client_route_calibration`、会话指纹/证明、UIA/OCR 兜底与显式失败状态 | 单元/集成回归；缺少唯一会话时 fail-closed |
| 发送语义 | 输入、发送、消息可见、ACK、结果回读分层记录 | 代码与回归；不以 UI 操作冒充 ACK |
| MCP 探测 | Windows Job Object 包住单次 probe worker/后代 | 实机只读超时 probe 回执 `process_tree_stopped_verified=true` |
| MCP 进程账本 | `coord-mcp-lifecycle status|prune`，run ID 防 PID 重用 | 生命周期专项测试；prune 只删记录，`processes_terminated=0` |
| 常驻控制 | Python supervisor 独占锁/心跳/tick；PowerShell 变薄包装 | supervisor 专项测试与 PowerShell 语法检查 |
| watcher | campaign 单实例、READY 握手、启动失败回收；improvement watcher 的停止轮询最多 1 秒 | watcher 9/9 回归 |
| 接口文档 | `coord-skill-manifest write` 重生成 92 命令清单 | manifest guard 进入全量回归 |

## 当前真实边界

唯一可用于宣布“真实队友已完成”的证据链是：

```text
SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

下列各项都不是 ACK：文件信箱存在、AEP order=`DEPOSITED`、MCP 进程存活、
窗口被发现、输入框获得文字、发送按钮被点击、或 supervisor 正在运行。

当前机器上仍存在的长时 `windows_mcp serve` 经父树核验分别归 Qoder 和
Qoder CN 当前宿主所有，CPU/内存占用低。不要用进程名、`conhost`、`python`
或命令行模糊匹配全机清理；那会中断活跃会话。AEP 只管理其自己创建的 probe
Job 与自己的账本记录。

## 后续执行者的首个工作顺序

1. `git status --short` 确认工作树和交接 commit 完整；不要覆盖用户本地的
   `hal-audit.txt`。
2. 运行 `python -m pytest -q`（项目运行时、PowerShell 7、`PYTHONUTF8=1`）。
3. 对每个目标客户端先做只读路线校准；窗口/会话不唯一则记录 `DEFERRED`，
   不能猜测或新建会话。
4. 只对用户明确授权的原会话投递无害 challenge，记录输入、发送、消息可见、
   目标生成的 `AEP-ACK` 和后续任务结果。
5. 若 `coord-desktop probe` 超时，只读其 receipt 中的 containment；禁止用
   全局 `taskkill` 清理其他宿主的 MCP。

## 常用命令

```powershell
$repo = 'K:\PythonProjects5\agent-evolution-protocol'
Set-Location $repo
$env:PYTHONPATH = "$repo\src"
$env:PYTHONUTF8 = '1'

# 只读：AEP 自己登记的 MCP 生命周期，不扫描/终止全机 MCP。
& .\.tools\agent-runtime\Scripts\python.exe -c "from agent_evolution_protocol.cli import main; raise SystemExit(main())" --coord-root coordination coord-mcp-lifecycle status

# 只读：一次 provider 工具发现；超时将回收本次 AEPProbe Job 子树。
& .\.tools\agent-runtime\Scripts\python.exe -c "from agent_evolution_protocol.cli import main; raise SystemExit(main())" --coord-root coordination coord-desktop probe --provider cursortouch --timeout 20

# 单次监督器 smoke test；状态不等于投递成功。
pwsh -NoProfile -File .\scripts\aep-supervisor.ps1 -MaxTicks 1 -NoPerceive
```

## 已知待办

- 用各客户端实际原会话完成并保存 ACK/结果回读；目前机制测试不能替代该验收。
- 以官方 API/CLI/CDP 为首选通道；UIA/OCR 保持为可审计的最后一层兜底。
- 继续隔离 Qoder、Qoder CN、WorkBuddy、WorkBuddy AI 与 CodeBuddy 的
  provider profiles，禁止把一个客户端的控件事实泛化到另一个客户端。
- 如需计划任务，先做 `-MaxTicks 1` 和 heartbeats 验证，再登记常驻任务；
  “任务已注册”不等于“Agent 已被唤醒”。
