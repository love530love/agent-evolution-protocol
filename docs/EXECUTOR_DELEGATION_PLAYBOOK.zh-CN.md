# 能力型 Agent 接力作战手册

## 目标

让没有浏览器、UI 或终端能力的 Agent，也能通过协作区调用具备能力的 Agent。调用方不需要知道对方使用哪种模型或工具，只提交意图、约束和验收标准。

## 标准链路

`能力发现 → 任务路由 → 有界唤醒 → 原会话接单 → 执行 → 证据回传 → ACK → 审计`

接力不创建新会话，除非任务状态明确允许；没有 `MESSAGE_VISIBLE` 和 `AGENT_ACK` 时，不得把“已发送”视为成功。

## 最小操作模板

```powershell
# 发现能力
aep coord-capabilities match --chip browser --strength browser-uia

# 发送有界唤醒
aep coord-wake --from codex --to qoder --task BROWSER-EXECUTION `
  --reason "读取原会话并完成浏览器检查，回传截图和 ACK" --budget bounded

# 汇总跨 Agent 回执
aep coord-chat-digest --agent codex --markdown
aep coord-delivery-audit --summary-only
```

## 执行者回执必须包含

- 原会话/窗口标识和事件 ID；
- 实际执行动作与时间；
- 页面或程序回读结果；
- 截图、日志或文件路径；
- `ACK` 或明确失败原因；
- 下一步建议及是否需要继续唤醒。

## 失败接力

执行者失败时不得静默退出。应发布 `warning` 或 `result`，附错误原文和已尝试动作；调度端再选择另一能力 provider 或另一 Agent。连续失败应暂停同类动作，避免重复消耗额度。

## 验收门槛

一次真实任务只有同时满足以下条件才算成功：

1. 目标 Agent 在原会话中确认接单；
2. 执行结果可由另一 Agent 读取；
3. 证据路径真实存在且可复核；
4. 任务状态为 `AGENT_ACK` 或更高完成态；
5. 失败、重试和权限模式均已写入协作日志。

本地小模型未安装时，以上链路仍必须可由规则路由和能力型 Agent 完成。
