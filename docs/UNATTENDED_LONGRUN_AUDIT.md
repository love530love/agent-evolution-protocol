# 无人值守长任务可靠性审计

## 结论

AEP 已具备会话唯一解析、多 transport、ACK lease、失败分类和 dead-letter 基础，
但“普通用户一句话交付一个跨数天复杂项目”还不能宣称完全闭环。还必须由编排器
持久化计划、依赖、预算和 checkpoint，并在重启后恢复，而不是依赖当前聊天窗口。

## 必须满足的验收条件

| 场景 | 必须证明 | 失败动作 |
|---|---|---|
| Agent 离线 | lease 过期、状态变为 offline | 切换能力匹配的备用 Agent |
| GUI 失焦/窗口重启 | PID/路径/anchor_digest 重新匹配 | 禁止坐标操作，进入重新发现 |
| 登录/权限失败 | 记录 provider 错误而非假成功 | 阻断该 transport，保留任务 |
| 消息送达 | 同一 event_id 的 delivered | 未送达不得推进阶段 |
| Agent 接单 | 同一 event_id 的 acknowledged | 超时走 recovery policy |
| Agent 崩溃 | checkpoint 与最后心跳可读 | 从最后安全 checkpoint 恢复 |
| 机器重启 | 计划、认领、幂等键仍存在 | 恢复未完成任务，不重复副作用 |
| 重复唤醒 | operation_key 去重 | 返回已有执行结果 |
| 跨天运行 | lease/预算/截止时间持久化 | 到期进入仲裁或 dead-letter |
| 最终交付 | 产物路径、校验和、验收证据齐全 | 不报告“完成” |

## 小白用户应看到的最小交互

用户只需提供目标、截止时间和允许的资源。系统自动生成：

1. 任务分解图和验收标准；
2. Agent 能力匹配与备用阵列；
3. 每阶段 checkpoint、预算和停止条件；
4. 无人值守执行计划；
5. 最终报告、产物索引和未解决风险。

只有需要登录、权限、真实业务决策或高风险外部副作用时才打断用户。

## 当前短板

- 编排器尚未统一接管所有 Agent 的长程 checkpoint；
- 真实 Qoder/WorkBuddy GUI 回合仍需各自适配器验证；
- “几天后完成”需要持久化 supervisor/计划任务，而非依赖当前进程；
- 最终交付物验收尚需统一 artifact manifest 和 checksum。

因此当前状态应标记为 `longrun_ready_with_adapters`，而不是无条件的
`fully_unattended`。任何宣称完成都必须带真实 ACK、产物和验收证据。
