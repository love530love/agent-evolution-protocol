# 事件驱动作战循环

这是一套不调用模型、不轮询聊天窗口的本地协调器。它把同题主攻、跨平台会诊、鲶鱼反证和独立审阅并行派给不同 Agent；任务结果或官方回表到达时生成下一轮，超时未 ACK/交付时交给候补。它**不上传包**，也不把文件投递、窗口聚焦或唤醒脚本运行当成 Agent 接单。

## 启动与日常命令

在仓库根目录使用 PowerShell 7；以下命令中的 `python` 应指向已安装本仓库依赖的环境。先开战役（任务号换成真实任务）：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m agent_evolution_protocol.coordination.campaign_cycle open --task T999 --goal '通过逐芯评测并形成可复核候选包' --lead codex --deputy workbuddy --cross-platform qoder --catfish hermes_desktop --reviewer codebuddy --context 'competition/results/T999.json'
```

查看 `status` 输出里的 assignment ID，目标 Agent 读信箱并自主 ACK、实验、回报：

```powershell
python -m agent_evolution_protocol.coordination.campaign_cycle status --task T999
python -m agent_evolution_protocol.coordination.campaign_cycle ack --task T999 --assignment-id '<ID>' --agent qoder
python -m agent_evolution_protocol.coordination.campaign_cycle result --task T999 --assignment-id '<ID>' --agent qoder --outcome completed --evidence 'logs/qoder-t999.txt' --hypothesis '拆分归约可避开编译失败' --next-step '交叉复核昆仑和华为'
```

`result` 的证据路径是引用，必须由审阅者核查。主攻失败自动给候补接力；主攻完成自动给独立审阅者。每个订单按 `cause` 幂等，防止回表重复导入后重复叫人。

每个 Agent 最多同时拥有两个待 ACK/执行中的任务，更多任务进入容量队列；已有任务完成、失败或超时后，由下一次文件事件/截止时间触发核对并释放队列。`scoreboard` 只依据有证据的完成/反证结果计分，超时记负分，在线时长与消息数量不计分：

```powershell
python -m agent_evolution_protocol.coordination.campaign_cycle scoreboard
```

官方回表必须先由人或可信采集器保存为 JSON，包含 `submission_id`、逐芯 `status`，失败芯还需 `failure_signature` 与 `raw_error` 或 `raw_error_path`：

```powershell
python -m agent_evolution_protocol.coordination.campaign_cycle official-return --task T999 --submission-id 12345 --package-sha256 '<64位sha256>' --snapshot-path 'competition/results/T999-12345.json'
```

导入时会派跨平台会诊、鲶鱼反证、主攻整合三条路线。同一芯同一失败签名在两次提交中重复出现，会记停火；要恢复投包仍须使用原有的独立复核、回归报告、额度与上传锁检查。导入器只检查快照结构，**不独立证明它确实来自官方**。

推荐在 PowerShell 7 使用管理入口启动文件事件监听器：

```powershell
& .\scripts\campaign-control.ps1 -Action start
& .\scripts\campaign-control.ps1 -Action status
& .\scripts\campaign-control.ps1 -Action stop
```

它按文件系统事件和最近截止时间核对作战状态，不进行固定间隔的模型/窗口轮询。若只想投递信箱、暂不执行桌面唤醒，启动时加 `-MailboxOnly`。管理入口记录 PID 和启动时间，拒绝误停不匹配的进程；如果记录陈旧，需要先人工检查再处理。启动前应先确认目标 Agent 的原会话适配器已验收；未验收者会保持待办或失败回执，不允许静默新建会话。

## 状态边界

`awaiting_ack` ≠ `active`；`active` ≠ `completed`；`completed` 也不等于可上传。上传权限始终是 `false`，必须走原有候选包证据门槛。Agent 可自行提出新路线，但需提交可证伪假设、最小实验、预测错误签名及证据；聊天量或在线时长不计成绩。唤醒适配器只负责尝试联系，真正接单由目标 Agent 的明确 ACK 证明。
