# 军团主动性契约与战术口令（2026-10-12）

目的不是让 Leader 微操所有 Agent，而是给成员**自主发现问题、提出异路、请求支援、紧急停火、竞争上场**的权利，同时让回表和额度约束及时改变行动。Leader 管目标、时序和交接；Deputy 管断线接力；跨平台专家、数值专家、性能专家和鲶鱼可在独立候选目录贡献。已知失败不准藏，诚实推翻自己假设不扣分。

## 口令与效果

```powershell
aep --coord-root <协作目录> coord-battle-order --agent codex --text '集结 T136' --deputy workbuddy --to qoder
aep --coord-root <协作目录> coord-battle-order --agent qoder --text '停火 T136：kunlun code299' --evidence <回表原文路径>
aep --coord-root <协作目录> coord-battle-order --agent codex --text '换防 T136' --evidence <交接摘要路径>
aep --coord-root <协作目录> coord-battle-order --agent qoder --text '复火 T136：kunlun code299' --evidence <新包SHA及回归报告路径> --reviewer qoder --fix-author codex
aep --coord-root <协作目录> coord-battle-order --agent workbuddy --text '开火准备 T136' --to codex qoder
aep --coord-root <协作目录> coord-battle-order --status-task T136
```

口令写入不可覆盖的 `battle_events/T136/`，并向当前 Lead/Deputy 与显式 `--to` 接收者的信箱投递同 ID 消息。`coord-delivery-status --to <代理> --id <事件ID>` 只核投递/ACK；**不自动启动模型、输入桌面、提交包或证明接单**。自然语言只允许以上固定句式，避免把聊天中任意一句话误触发真实动作。

`集结`不能抢已存在的 Leader；无 Deputy 时必须先找候补。`停火`允许任一成员提出，但必须附失败证据；重复签名只留一个活动停火，阻止刷消息。`换防`仅当前 Lead 可发，且需要交接证据。`复火`须精准对应一条活动停火，**由非修复作者本人以其 Agent 身份发出**，附修复/回归证据；不得由修复作者代填别人的 reviewer 名字。复火**仅解除本地停火状态**，不是上传批准。`开火准备`遇活动停火会拒绝。波次规划器也会把活动停火的题目列入 `held`。

## 权利、责任与激励

- 每位成员有独立候选与异议权；应预注册可证伪预测、最便宜实验和停止条件。Lead 不得凭资历或题号认领压制有证据的异路。不能覆盖他人目录或跳过外部写入租约。
- 回表先共享官方事实，独立假设可以在短暂预注册前隔离；之后交叉会诊。不同能力互补，强制关注编译、精度、性能、规则和榜单估价五种失效面。
- 奖励首次有效行、跨芯根因、准确预测、使他人少投一次的失败归纳、可靠交接和可复用修复；不奖励刷消息、刷同字节包、窗口聚焦或盲目“积极”。高信息量的首次失败是正贡献；明知同错仍投、未 ACK 冒称接单、无证据压制路线才需复盘。
- 一轮完成后，Lead/Deputy 必须留下一张可执行接力卡：当前有效行、未解芯片、已排除假设、冻结包 SHA、下一最小实验、额度/排期与明确停止条件。下一波依据回表重算，不让旧队列自动连射。
- 连续两题协作指标退化，用 `coord-mechanism-audit` 生成可逆机制挑战；小范围验证后再改指挥通道或提示词。协议不因“已写文档”自动宣称 1+1>2。

## 无人值守边界

本地文件事件/Hook 可以无模型 token 地驱动战况投递；未 ACK 时保留待办或交由已接单 Deputy。WorkBuddy/Qoder 目前尚未完成“准确投递旧会话—读回—Agent ACK”的闭环验收，不能把信箱消息或聚焦窗口当作真正自动唤醒。真实上传仍需实时额度、间隔、幂等、已验证跨进程租约与当前授权；本契约不改变 `SHADOW/HUMAN_HOLD`。
