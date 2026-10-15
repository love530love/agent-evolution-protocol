# 红皇后—鲶鱼—创造性破坏：多 Agent 自治协作与进化体系

总体系名称保持不变。AEP 是其协议、工具与实现载体；“协同求真”描述工作流追求的质量，不是替换原体系的新品牌。

## 三种动力与工作流

| 动力 | 核心问题 | 运行方法 |
| --- | --- | --- |
| 红皇后 | 相对外部环境，我们是否在退步？ | 有来源与时效的外部信号、单 Agent 基线、差距与结果追踪 |
| 鲶鱼 | 共同相信的解释可能错在哪里？ | 独立首答、异路保留、反证、复现、偏差审计与裁判复核 |
| 创造性破坏 | 哪个新方案足以替换旧方案？ | 冻结候选、判别实验、实证择优、版本化采纳与回滚 |

默认循环为：任务契约 → 独立探索 → 冻结 → 分歧定位 → 判别实验 → 选择集成 → 执行验收 → 机制复盘。唤醒、消息、上下文、资源租约与激励支撑此循环。

[协同推理与行动工作流](COLLABORATIVE_REASONING_FRAMEWORK.zh-CN.md)是总体系下的工作流分册，逐项完善用户提出的 16 条方法。原有[反坍塌协议](coordination/MULTI_AGENT_ANTI_COLLAPSE_PROTOCOL.md)保留其试点状态与适用范围；新版总览本身不改变旧任务的 HOLD。

## v0.3.0 能力与边界

已实现：本地信箱与证据分级、冻结候选门禁、角色干预登记、五角色鲶鱼评审链、独立裁定后 AP/RP 与长期声誉、重复证据防刷分，以及本次新增的冻结任务契约。

仍需实机验收或后续实现：各客户端原会话稳定唤醒与 ACK、真实身份认证、独立审议到明确采纳的完整闭环、资源调度与激励的因果评估。任务契约记录预算与授权声明，尚不自动监控时间或拦截全部执行器。理论工作流、单测通过、客户端送达和真实任务胜率分别登记。

## 让新 Agent 用起来

仍从项目原有 `join` / `route` 入口接入。复杂任务再创建一份冻结契约：

```powershell
python -m agent_evolution_protocol --coord-root .\coordination coord-reasoning create --request-file .\examples\reasoning-contract.json
python -m agent_evolution_protocol --coord-root .\coordination coord-reasoning status --task DEMO
```

先按实际目标修改示例。契约要求目标、验收、负责人、模式、交互方式、讨论轮数/时间预算及停止条件。模式为 `light`、`standard`、`assault`；交互为 `guided`、`checkpoint`、`delegated`。首次创建后不可覆盖；修订须使用新任务版本 ID，保留旧记录及其来源。

契约目录为 `reasoning_contracts/<task>/contract.json`，读取时校验内容摘要。摘要用于发现意外改动，不提供身份认证或对抗文件篡改能力。后续依次使用现有 `coord-adversarial`、`coord-evolution`、`coord-governance` 与 `coord-legion` 命令完成对应工作；每一步以实际产物、证据和状态为准。

## 版本关系

- [演化历史](EVOLUTION.md)：理念与实施过程。
- [专家审议记录](coordination/EXPERT_EVOLUTION_REVIEW_20261015.md)：角色、积分与治理的取舍。
- [发布说明](releases/v0.3.0.md)：本次交付与验证范围。

后续优先打通真实指令接收、任务状态与阶段门，再以同类任务对照评估净收益。评价重点为任务成功率、错误复发、完成成本和用户干预，而非消息数、角色数或共识率。
