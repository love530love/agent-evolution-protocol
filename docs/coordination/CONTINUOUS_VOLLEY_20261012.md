# 连续波次攻坚与紧急停火（2026-10-12）

“弹药”是已冻结、已核验且有独立评审的候选包，不是目录里随便一个 ZIP。Lead 统筹一题的会诊与回表，Deputy 在失联时接力；不同 Agent 的异路包可以同场竞争。额度未刷新时继续诊断、跨芯编译、分拆 kernel、回归和打包，不空等；但**本工具只给排程建议，不执行上传**。

## 波次原则

1. 读候选 manifest、最新官方结果和当前额度，保留安全额度 `reserve`；每波至多 `wave_size` 个不同题的候选。同一题只规划一包，其余置 `reload_wait` 装填待命，须等该题回表后重新规划。可在平台间隔允许时推进另一题，不能越过实际提交间隔。
2. 同一个 SHA 的包只算一份弹药；同候选已提交不得重发；任何 `UNKNOWN` 提交先查平台 ID，不重投。
3. 若同题同芯的相同失败签名出现在两笔不同提交，立刻对该题触发 `repeat_failure_ceasefire`。其他题可继续；受影响题须给出针对该签名的修复说明和回归报告，由非作者复核，再进入新规划。不能仅改 ZIP 名字解除停火。
4. 每次回表立即更新病例、失败签名和候选库，重新运行规划器。失败若增加了可迁移信息，保留证据与贡献；如果只是重演已知错误，冻结该路线而非刷额度。
5. 真正上传前逐笔重新核对规则、本地/跨芯门禁、包 SHA、额度、时间间隔、平台最新状态、跨进程安全的意图租约和幂等键。当前这些外部闸门尚未被本规划器自动证明，因此输出状态固定为 `PROPOSAL_ONLY_NOT_AUTHORIZED`。

```powershell
aep coord-volley-plan --manifest-file <候选数组.json> --results-file <逐芯回表数组.json> --quota-remaining 12 --reserve 2 --wave-size 3
```

候选字段：`candidate_id`、`task`、`agent`、`package`、`sha256`、`priority`、`rule_review`、`local_gate`、`cross_platform_review`、`package_review`、`independent_reviewer`。如需解除重复失败停火，还须 `fixes`（以归一化错误签名为键）及 `regression_report`。结果字段至少含 `task`、`submission_id`、`candidate_id`、`chip`、`status`，失败时含 `failure_signature`；`UNKNOWN` 状态会暂停该题规划。输入需有可信来源，规划器只验证格式、ZIP 内容和本地 SHA，不宣称这些评审文档真实有效。

静默时不周期调用模型。回表 Hook、人工确认或 Agent 下一次进入任务时触发重算；无法安全投递原会话则留待办与交接，不新开一堆窗口。连续两题波次周转率或重复错误率退化时，调用 `coord-mechanism-audit` 提出可逆机制挑战。
