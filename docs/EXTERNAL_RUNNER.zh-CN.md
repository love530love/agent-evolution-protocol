# 本地执行入口与恢复账本

`coordination.external_runner` 将外部CLI计划推进为真实子进程执行。使用参数数组、UTF-8 stdin、明确工作目录和超时；执行前独占创建 attempt 文件并 fsync。同事件同请求返回原结果，同事件不同请求拒绝。执行器崩溃留下 UNKNOWN，不自动重放。退出码0记录 PROCESS_EXITED，不能冒充模型完成。

```powershell
$env:PYTHONPATH=Join-Path (Get-Location) 'src'
python -m agent_evolution_protocol.coordination.external_runner desktop-read --root coordination/external_runs --workspace . --event-id EVT-DESKTOP-READ-001 --apply
python -m agent_evolution_protocol.coordination.external_runner t3-send --root coordination/external_runs --workspace . --event-id EVT-T3-001 --session-id <既有线程ID> --text-file task.txt --apply
python -m agent_evolution_protocol.coordination.external_runner worker-resume --root coordination/external_runs --workspace . --event-id EVT-WORKER-001 --agent-type qoder --session-id <原生CLI会话ID> --text-file task.txt --apply
```

去掉 --apply 输出计划。本入口不创造原生会话、不代替AEP任务认领与租约；同一会话须由上层既有调度串行调用。CLI会话ID必须实际发现，不能把桌面聊天ID直接代入。LobeHub仅运行本地hetero，不接服务器。桌面入口当前仅只读枚举；写操作继续走已验收的 AEP UIA 路径。

结果全文保存在本地result.json。后续结果判读器需依据工具活动、目标回复及产物证据升级状态；目前不自动宣称AGENT_ACK或VERIFIED。
