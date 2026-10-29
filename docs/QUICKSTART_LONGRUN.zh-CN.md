# 小白用户：启动长期多 Agent 任务

在项目目录执行：

```powershell
pwsh -File .\scripts\start-mission.ps1 `
  -Goal '完成一个可验证的复杂交付' `
  -Workspace 'K:\\your-workspace' `
  -Deadline '2026-12-31' `
  -Budget '100 次模型回合' `
  -Deliverable 'report.md','tests.txt' `
  -Constraint '禁止盲目外部提交'
```

命令会返回 `mission_id`。之后调度器围绕这个 ID 分工、唤醒、接力、恢复和验收。
用户不需要分别向每个 Agent 转述上下文；需要人工决定时才会收到通知。
