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
