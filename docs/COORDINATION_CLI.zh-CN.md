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
aep workspace-init --workspace K:\PythonProjects5\FlagGems-sglang --agents-md
aep --coord-root coordination --kernel-root .aep-kernel inspect --markdown
aep --coord-root coordination --kernel-root .aep-kernel join --agent hermes_desktop --task T125 --workspace K:\PythonProjects5\FlagGems-sglang --markdown
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
aep --coord-root coordination coord-guided-retry --attempted-action "submit upload" --error "timeout" --risk-level high
```

## 恢复内核 / 资源锁命令

当多个 agent 可能同时操作同一浏览器 Tab、同一文件、同一上传额度或同一外部平台时，使用 `kernel-*` 命令记录可恢复的资源锁和幂等操作：

```powershell
aep --kernel-root .aep-kernel kernel-status
aep --kernel-root .aep-kernel kernel-lease-acquire --lease-type BROWSER_SESSION_LEASE --resource tab:1 --executor codex --ttl-seconds 300
aep --kernel-root .aep-kernel kernel-lease-release --lease-type BROWSER_SESSION_LEASE --resource tab:1 --executor codex --lease-epoch 1
aep --kernel-root .aep-kernel kernel-op-reserve --operation-key upload:T125:r1 --request-json '{"task":"T125","artifact":"sha256..."}'
aep --kernel-root .aep-kernel kernel-op-transition --operation-key upload:T125:r1 --state STARTED --evidence-json '{"started_by":"codex"}'
aep --kernel-root .aep-kernel kernel-op-transition --operation-key upload:T125:r1 --state COMMITTED --evidence-json '{"receipt":"ok"}'
aep --kernel-root .aep-kernel kernel-session-bind --agent workbuddy --provider hermes --workspace K:\PythonProjects5\FlagGems-sglang --session-id "<existing-session-id>"
aep --kernel-root .aep-kernel kernel-continuation-plan --agent workbuddy --workspace K:\PythonProjects5\FlagGems-sglang
```

设计原则：

- 读操作可以并行，写操作必须持有 lease。
- 高风险外部动作先 `kernel-op-reserve`，执行中转 `STARTED`，结果明确后转 `COMMITTED` 或 `ROLLED_BACK`；结果未知时转 `UNKNOWN`。
- 续跑前先看 `kernel-continuation-plan`，不要因为找不到旧会话就自动开新会话。
- 长任务中断前写 `kernel-checkpoint`，让另一个 agent 可以从 evidence 和 next step 接续。

## 协作纪律

- 普通消息不唤醒模型。
- 新 agent 先读 digest / status / onboarding，再行动。
- 默认禁止新建会话；优先使用 sticky session。
- 缺少 sticky `thread_id` 时，不要自行开窗；写 digest 或状态板并等待人工绑定。
- wake 请求必须有任务、理由、预算和停止边界。
- 归档旧 wake 只表示“停止按旧请求执行”，不表示任务完成。
- 写动作前先 `coord-claim`；长任务要用 `coord-task-state` 留下 phase、goal、evidence、next-action。
- 失败后先用 `coord-guided-retry` 分类；高风险或结果未知的写动作不得盲目重放。

## 和治理协议的关系

`coord-*` 命令负责协作平面：消息、digest、wake、session、队列。

`init / commit / reveal / challenge / threat / decide / audit` 负责竞争与治理平面：红皇后、鲶鱼、创造性破坏、证据链和决策审计。

## 进一步阅读

- [新 Agent 接入契约](AGENT_ONBOARDING_CONTRACT.zh-CN.md)
- [Guided Retry 策略](GUIDED_RETRY_POLICY.zh-CN.md)
- [`aep inspect` 作战简报](RUNBOOK_INSPECT.zh-CN.md)
- [新 Agent 如何接入任意任务](JOINING_ANY_TASK.zh-CN.md)
- [自然语言接入](NATURAL_LANGUAGE_JOIN.zh-CN.md)
