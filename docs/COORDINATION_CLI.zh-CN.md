# 多 Agent 协作 CLI 快速指南

本仓库现在提供统一入口 `aep`（等价于 `agent-evolution`），用于把分散的协作脚本收束到一个本地、可审计、无模型调用的命令行。

## 初始化

```powershell
pwsh.exe -File .\scripts\bootstrap.ps1 -CoordRoot coordination
```

这会安装 editable 包、创建本地协作目录，并运行自检。

## 常用命令

```powershell
aep --coord-root coordination doctor
aep --coord-root coordination coord-status
aep --coord-root coordination coord-digest --agent workbuddy
aep --coord-root coordination coord-session-set --agent workbuddy --thread-id "<existing-thread-id>"
aep --coord-root coordination coord-send --from codex --to qoder --topic T125 --kind update --text "read latest digest first"
aep --coord-root coordination coord-wake --from codex --to qoder --task T125 --reason "bounded one-shot check" --budget bounded
aep --coord-root coordination coord-wake-status
aep --coord-root coordination coord-claim --agent codex --task T125 --paths src/operator.py
aep --coord-root coordination coord-task-state --agent codex --task T125 --phase working --goal "run cheapest falsification" --next-action "publish result digest"
aep --coord-root coordination coord-release --agent codex --task T125
aep --coord-root coordination coord-archive-stale --older-than-hours 24 --archive-name archived-stale
aep --coord-root coordination coord-onboarding --agent hermes_desktop
```

## 协作纪律

- 普通消息不唤醒模型。
- 新 agent 先读 digest / status / onboarding，再行动。
- 默认禁止新建会话；优先使用 sticky session。
- 缺少 sticky `thread_id` 时，不要自行开窗；写 digest 或状态板并等待人工绑定。
- wake 请求必须有任务、理由、预算和停止边界。
- 归档旧 wake 只表示“停止按旧请求执行”，不表示任务完成。
- 写动作前先 `coord-claim`；长任务要用 `coord-task-state` 留下 phase、goal、evidence、next-action。

## 和治理协议的关系

`coord-*` 命令负责协作平面：消息、digest、wake、session、队列。

`init / commit / reveal / challenge / threat / decide / audit` 负责竞争与治理平面：红皇后、鲶鱼、创造性破坏、证据链和决策审计。
