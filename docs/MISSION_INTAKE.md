# Mission Intake

统一长期任务入口的最小契约：

```text
启动长期任务：
目标：完成一个可验证的交付
工作区：K:\\project
截止：2026-12-31
预算：100 次模型回合
限制：禁止盲目外部提交
交付：报告、代码、测试证据
```

`mission_intake.create()` 会生成稳定的 `mission_id` 和三个最小阶段：
`intake → execution → delivery`。后续 planner 可以把 execution 拆成 DAG，
而唤醒、认领、checkpoint、ACK 和最终验收都必须携带 `mission_id`，防止长任务
在不同 Agent 之间丢失上下文。

最终交付还必须通过 artifact manifest：每个声明产物都要存在，并记录大小和
SHA-256；文件缺失或内容变化时，mission 不能标记为最终完成。

监督器只生成动作建议（`assign`、`observe`、`recovery_review`、`report_delivery`），
不代替 Agent 执行高风险操作，也不靠固定频率空转。调度器在有事件、租约到期或
用户要求时调用一次 tick。

基础规划器会先生成 `research → implementation → validation → delivery` 的依赖链。
专家 Agent 可以提出细化或替代方案，但不能删除既有证据；规划变更必须保留版本和
原因，避免长任务在多 Agent 交接时失去主线。
