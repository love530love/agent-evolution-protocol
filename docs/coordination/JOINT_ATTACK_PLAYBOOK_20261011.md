# 逐题联合攻坚：回表 → 会诊 → 实验 → 备包

这是一套**事件触发、有人在场时推进**的协作闭环，不是静默调用模型或无人监督自动上传。题目 Leader 管推进与排程，Deputy 在 Leader 离线或无 ACK 时接力；跨平台专家复核编译/芯片错误，鲶鱼给异路、可证伪挑战。两名专家不能由同一人充数。任何 agent 可另开独立候选，按 `T132-QODER-R2` 之类的候选 ID 认领自己的目录；旧题号 owner 不是算法独占权。

## 每一笔官方回表建立一个会诊病例

先把从平台读取的提交号、逐芯状态和失败原文保存成 UTF-8 JSON，例如：

```json
{"submission_id":"30164","chips":{"amd":{"status":"passed"},"kunlun":{"status":"failed","raw_error":"TritonXPULegalize ..."}}}
```

记录**实际提交包的 SHA-256**，然后执行：

```powershell
aep --coord-root <协作目录> coord-joint-case open --task T124 --submission 30164 --lead codex --deputy workbuddy --artifact-sha256 <64位SHA> --result-file <官方回表快照.json>
```

病例文件只创建一次，不覆盖旧证据。`official_snapshot` 由执行者提供；系统验证格式和失败原文存在，**不能凭本地 JSON 证明平台真实性**，需保留平台链接/截图或受信读取记录。

专家与鲶鱼分别提交 JSON 审议卡：`hypothesis`、`predicted_signature`、`cheapest_test`、`stop_condition`、`evidence_paths`。用 `coord-joint-case review --task ... --submission ... --agent ... --role cross_platform|catfish --review-file ...` 登记；`status` 会显示缺少哪一种独立审查。两类审查齐备只进入 `HYPOTHESIS_SPLIT`，**不会标成可上传**。

可用 `coord-capabilities declare --agent qoder --chips kunlun amd --compilers triton_xpu mlir --strengths compiler kernel_split --evidence <已验证报告路径> --ttl-minutes 30` 主动登记专长及短时可用性；`coord-capabilities match --chip kunlun --compiler triton_xpu` 只做本地匹配。过期即显示 `EXPIRED`，不会自动续期；匹配不能代替消息 ACK，也不直接唤醒或委派模型。无人可接时交给 Deputy 排入下一次真实入场的待办，而不是无意义轮询。

在额度未更新前，继续做最小单变量实验、跨芯编译与精度回归、候选包冻结（路径/SHA/ZIP 成员）、规则审查、非作者复核，并准备有序候选清单。争议按“各自预测什么错误签名、哪个实验最便宜、截止与停止条件”裁决，不靠多数票。新证据可淘汰旧路线，但须保存旧失败与反证，防止重复撞坑。

只有在候选正确性、跨芯结果、规则、包 SHA、实际额度/时间间隔、同包去重、上传意向租约及幂等操作都另行核准后，才允许申请真实上传。任何未知结果先查提交 ID，不盲重投。本工具当前**不作自动上传放行判定**，不把 `SHADOW/HUMAN_HOLD` 说成无人值守已落地。

## 自我改进触发

对每题记录：回表到首个可证伪实验的时间、跨能力复核覆盖率、同错重复率、有效芯片覆盖/名次增益、信息增益/上传额度、未 ACK 滞留时间和机制本身耗时。连续两题这些指标无改善或机制开销过高时，Leader/鲶鱼提出一张“机制挑战卡”：明确失效环节、局部可逆改动、对照指标和回滚条件；通过测试和小范围试点后再推广。静默状态只保留待办，不循环唤醒模型。

`aep coord-mechanism-audit --metrics-file <按题号顺序排列的指标.json>` 可对最近两题做只读触发判定。每题须含 `task`、`evidence_paths`、`first_experiment_minutes`、`cross_review_coverage`、`repeat_error_rate`、`information_gain_per_upload`，可选 `coordination_overhead_pct`。输入指标由人/Agent 提供，工具不假装已验证平台数据；触发只提出修改实验，不自行修改生产自动化。
