# 无人值守监督与回访闭环

## 目标

AEP 不仅发送一次消息，还要持续推动任务直到完成、转交或明确失败。监督器只关注已有任务和事件，不扫描所有 Agent，不对静默状态进行无意义模型轮询。

## 生命周期

```text
DEPOSITED
  → WAKE_QUEUED
  → WINDOW_FOUND
  → MESSAGE_VISIBLE
  → AGENT_ACK
  → WORKING
  → EVIDENCE_RETURNED
  → VERIFIED / RETRY / HANDOFF / FAILED
```

每次状态转换必须带事件 ID、时间、执行者和证据路径。`MESSAGE_VISIBLE` 之前不允许进入工作中；`AGENT_ACK` 之前不允许计为接单。

## 有界监督

```powershell
# 只监督已有任务，不创建新会话
aep coord-commander watch --agent qoder --interval 30 --max-ticks 20

# 查看待处理和下一步证据
aep coord-delivery-audit --summary-only
aep coord-wake-status --tail 20
```

监督器只在以下事件发生时动作：任务到期、Agent 未 ACK、执行失败、证据缺失、回表更新或新消息到达。没有变化时不发送消息、不唤醒模型、不消耗额度。

## 回访与回表

执行者完成后必须提交结果和证据。监督器验证证据存在，再触发一次回访；若仍无 ACK，则将任务转给具备同类能力的 Agent。连续失败达到策略阈值后暂停同类操作并发布告警，避免批量误操作。

## 监督责任

- 调度 Agent：保持主线、选择执行者、控制预算；
- 执行 Agent：完成电脑/浏览器/终端动作，回传证据；
- 鲶鱼 Agent：质疑假设、检查遗漏和重复失败；
- 回表 Agent：读取外部结果，确认成功或生成下一轮修复任务。

本地模型只负责辅助分类和提醒；电脑操作始终通过已注册能力 provider 或具备该能力的 Agent 完成。
