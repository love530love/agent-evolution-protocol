# Plannotator 评审适配器

本适配器将 AEP 的计划、工件索引和证据导出为带 `workspace_id` 与 `event_id`
水印的 Markdown/JSON 包，供 [Plannotator](https://github.com/backnotprop/plannotator)
或任何 Markdown 审阅工具打开。它不依赖 Plannotator 二进制，不安装全局 hook，
不启动服务，也不操作桌面。

```powershell
pwsh -File scripts/aep-plannotator.ps1 export `
  -WorkspaceId W-0123456789abcdef -EventId PLAN-001 `
  -Title '桌面执行方案' -PlanFile .\plan.md

pwsh -File scripts/aep-plannotator.ps1 decision `
  -WorkspaceId W-0123456789abcdef -EventId PLAN-001 `
  -DecisionId REVIEW-001 -Decision revise -Reviewer reviewer `
  -Summary '补充原会话 ACK 的验收条件'
```

导出写入 `coordination/plannotator/exports/`，决策写入
`coordination/plannotator/decisions/`。同一事件的导出不可静默替换；发生重大修改
必须创建新的修订事件。`approve` 仅表示评审意见，不会自动创建订单、派发消息或
宣称 Agent ACK。后续调度器若要采用决定，必须以独立的、可审计的 AEP 状态转换实现。

上游为 MIT 或 Apache-2.0 双许可证；若复制上游代码，须保留其版权与许可声明。
本适配器目前只互操作 Markdown/JSON，不复制上游代码。
