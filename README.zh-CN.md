# Agent Evolution Protocol

一套防止多 Agent “越协作越趋同”的证据驱动治理协议。

## 第一次接触？

先阅读 [《初学者介绍：为什么多 Agent 越协作，反而可能越平庸？》](docs/INTRODUCTION.zh-CN.md)。它通过一个具体案例解释设计初衷、角色边界、完整回合、经济学来源和第一次运行方法。

最简短的理解是：**公共事实立即共享，候选假设先独立形成；全部承诺后再揭示，最终让可复现实验而不是多数投票选择路线。**

它组合三种动力：

- **红皇后压力**：只响应有来源的外部变化，不制造永久紧急状态；
- **鲶鱼挑战**：保留有预算上限、由事件唤醒的异议者；
- **创造性破坏**：通过预注册实验淘汰候选，不靠领导偏好或多数投票。

系统把公共事实、ownership、预算、SHA 和已验证结果放在协调平面；把候选假设放在隔离探索岛中，全部 commit 后再 reveal，随后进行最低成本证伪和逐轮淘汰。

仓库内 CLI 仅用于本地影子审计，不调用模型、不轮询、不运行 GPU、不自动改代码或提交外部平台。完整规则见 [协议](docs/PROTOCOL.md)，演化过程见 [迭代回溯](docs/EVOLUTION.md)。

## 协作内核

仓库还包含一套本地事件驱动的多 Agent 协作参考实现：

- [协作机制演化记录](docs/coordination/MULTI_AGENT_COLLABORATION_EVOLUTION.md)
- [状态板协议](docs/coordination/MULTI_AGENT_STATUS_BOARD.md)
- [唤醒内核](docs/coordination/MULTI_AGENT_WAKE_KERNEL.md)
- [会话亲和机制](docs/coordination/MULTI_AGENT_SESSION_AFFINITY.md)
- [反平庸/反坍缩协议](docs/coordination/MULTI_AGENT_ANTI_COLLAPSE_PROTOCOL.md)
- [红皇后—鲶鱼—创造性破坏复盘](docs/coordination/RED_QUEEN_CATFISH_CREATIVE_DESTRUCTION_RETROSPECTIVE_20260928.md)

参考代码位于 `src/agent_evolution_protocol/coordination/`。设计原则是：普通消息只留言，
不会启动模型；只有显式 wake request 才进入可审计的唤醒队列，并通过 adapter 触达具体桌面 Agent。

## 统一本地 CLI

第一次使用可以直接运行：

```powershell
pwsh.exe -File .\scripts\bootstrap.ps1 -CoordRoot coordination
```

安装后可以使用 `aep`（等价于 `agent-evolution`）：

- 治理平面：`init`、`commit`、`reveal`、`challenge`、`threat`、`decide`、`audit`
- 协作平面：`doctor`、`workspace-init`、`inspect`、`route`、`join`、`coord-status`、`coord-digest`、`coord-send`、`coord-wake`、`coord-wake-status`、`coord-archive-stale`、`coord-session-set`、`coord-claim`、`coord-release`、`coord-task-state`、`coord-onboarding`、`coord-guided-retry`
- 恢复内核：`kernel-status`、`kernel-lease-acquire`、`kernel-lease-release`、`kernel-op-reserve`、`kernel-op-transition`、`kernel-session-bind`、`kernel-continuation-plan`、`kernel-checkpoint`

协作 CLI 只读写本地文件，不调用模型。它的目标是让新加入的 agent 先读 digest、状态板和 sticky session 策略，再决定是否行动；如果缺少长期会话 ID，就暴露为 handoff 问题，而不是默默新建无意义会话。

更多命令示例见 [多 Agent 协作 CLI 快速指南](docs/COORDINATION_CLI.zh-CN.md)、[新 Agent 接入契约](docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md)、[Guided Retry 策略](docs/GUIDED_RETRY_POLICY.zh-CN.md)、[`aep inspect` 作战简报](docs/RUNBOOK_INSPECT.zh-CN.md)、[新 Agent 如何接入任意任务](docs/JOINING_ANY_TASK.zh-CN.md)、[自然语言接入](docs/NATURAL_LANGUAGE_JOIN.zh-CN.md) 和 [`aep route` 需求调研与开发计划](docs/ROUTE_DEVELOPMENT_PLAN.zh-CN.md)。

## 一句话让新 Agent 接入

共享项目初始化一次：

```powershell
aep workspace-init --workspace 'K:\PythonProjects5\MyProject' --agents-md
```

之后给新 agent 一句话：“请加入这个工作区协作，接入 T125。”支持项目指令的 agent 会从 `AGENTS.md` 找到 `AEP_JOIN.md`；不自动读入口的产品，补一句“先读根目录 AEP_JOIN.md”。命令型 agent 按原话获取协作指引：

```powershell
aep route --agent hermes --workspace 'K:\PythonProjects5\MyProject' --intent '请加入这个工作区协作，接入 T125' --markdown
```

同一入口支持了解项目、加入任务、接手、只审查、独立探索、共享资源操作、失败恢复、唤醒和状态汇报。任务不限赛题，可用 `--task BUG-42` 或 `--task DOCS-INTRO`。项目习惯用语放入可选 `.aep/routing.json`；决策 trace 可帮助解释命中和歧义。`route` 只给建议，实际认领、资源租约和执行均有独立步骤。

长程任务优先复用已有会话，先读摘要和最新检查点，需要时再展开历史。只读文件的 agent 也能参与审查和分析，由命令型 agent 协助完成认领或租约。添加 agent 名称不等于接通产品适配器；真实唤醒、原窗口续接仍需对应产品支持。完整示例和能力边界见[自然语言协作路由](docs/SCENARIO_ROUTER.zh-CN.md)。
