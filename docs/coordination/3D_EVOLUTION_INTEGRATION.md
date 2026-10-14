# 从 3D 项目引入的自治进化门禁

来源：[`love530love/3D`](https://github.com/love530love/3D)，本地审阅提交 `0bdf53a771824eaba85441ccf9addee1c47743fc`。借鉴的是数据/治理方法，不移植彩票预测模型或历史数据。

该项目的可复用纪律：先保存原始证据，冻结候选再看后续结果；与透明基线比较；漂移或退化触发提案；达到数值门槛也只进入专家审查，不自动替换正式基线；风险操作具备回滚和冻结路径。

AEP 的 `coord-evolution` 实现一个保守闭环：

1. `coord-mechanism-audit --metrics-file <任务指标序列>` 先判定是否有退化触发。任务指标与证据路径必须由操作者核实；工具不声称官方成绩属实。
2. `coord-evolution propose --metrics-file <同一文件> --change <单一可逆改动> --rollback <回滚步骤> --author <agent>` 冻结原文件 SHA-256、上一任务基线和提案。相同证据与改动不会生成重复提案。没有触发、缺证据或缺回滚方案则拒绝。
3. 在后续独立任务上观察候选，先将指标和证据写成 JSON，然后 `coord-evolution evaluate --proposal-id <ID> --candidate-file <文件>`。若原证据改变则拒绝；四项指标不得退化且至少一项严格改善才进入 `EXPERT_REVIEW`，否则 `BASELINE_REQUIRED`。
4. `EXPERT_REVIEW` **不是生效**。独立专家审议、回滚演练和明确采纳仍是后续步骤。该命令不会改写生产指挥配置、自动唤醒 Agent、自动投包或解除 HOLD。

这让红皇后持续提出挑战，鲶鱼推动可证伪实验，同时保留安全基线。下一阶段可接入更长时间窗、反事实对照、审计签名和事件驱动的提案提醒；在有可靠实机数据前，不应自动晋升新策略。
