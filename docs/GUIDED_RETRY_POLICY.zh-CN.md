# Guided Retry 策略

目标：agent 尝试失败后，不应直接中断，也不应盲目重试。失败必须先分类，再决定观察、等待、修复、人工确认或停止。

## 分类

| 分类 | 典型信号 | 默认动作 |
|---|---|---|
| `uncertain-outcome` | timeout、disconnect、unknown、busy | 不重放；检查状态；外部写可能发生时标记 `UNKNOWN` |
| `resource-conflict` | lease conflict、locked、claimed | 等待或 handoff；不要绕过 owner |
| `stale-observation` | stale ref、element not found、covered、obscured | 重新 observe；低风险可重试；高风险需确认 |
| `permission-or-auth` | 401、403、unauthorized、credential | 停止；请求修复授权 |
| `bad-request` | 400、schema、validation | 本地修复请求；payload 变化则新 operation key |
| `unknown` | 未知错误 | 写 checkpoint；中高风险需人工确认 |

## CLI

```powershell
aep --coord-root coordination coord-guided-retry `
  --goal "upload package" `
  --attempted-action "browser_upload then submit" `
  --error "timeout after click" `
  --risk-level high
```

## 和 kernel operation 的关系

- 操作未开始：保持 `RESERVED` 或 `ROLLED_BACK`。
- 操作已开始但结果不明：转 `UNKNOWN`。
- 操作确认成功：转 `COMMITTED`。
- 操作确认未发生或已撤销：转 `ROLLED_BACK`。

## 最小恢复包

长任务失败或中断时，至少留下：

- completed
- next_step
- artifacts
- pending_operations
- unknowns

对应命令：

```powershell
aep --kernel-root .aep-kernel kernel-checkpoint --task-id T125 --executor codex --payload-file checkpoint.json
```
