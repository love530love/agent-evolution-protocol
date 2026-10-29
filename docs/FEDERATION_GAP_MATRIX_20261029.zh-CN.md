# 持久联邦工作区引擎｜现状与缺口矩阵

状态定义：`已落地` = 有代码和测试；`部分落地` = 有骨架或局部实测；`待真实验收` = 不能仅靠单元测试证明。

| 宪章能力 | 当前证据 | 状态 | 下一步 |
|---|---|---|---|
| Workspace 唯一持有者 | mission runtime、session registry、mission graph | 部分落地 | 全部事件/消息/工件统一写入 `workspace_id` 水印 |
| A2A + MCP 互通 | team dispatch、transport catalog、MCP/桌面适配器、per-Agent MCP Bundle registry | 部分落地 | 固定 A2A envelope、能力发现和跨宿主 readback 合约，并逐 Agent 绑定原会话 |
| 结构化快照接力 | handoff worker、artifact manifest、checkpoint | 部分落地 | 引入不可变 snapshot schema、版本、父快照和恢复校验 |
| 原会话精准唤醒 | UIA、CDP、provider readback | 部分落地 | Qoder/WorkBuddy/WorkBuddy AI 各完成原会话发送+ACK验收 |
| Token/进程/卡死接管 | recovery policy、wake daemon、mission supervisor | 部分落地 | 故障分类、心跳租约、自动改派和副作用冻结 |
| 7x24 跨重启恢复 | 持久目录、事件监听、mission state | 部分落地 | Windows 服务/计划任务安装器、重启演练、断点续跑验收 |
| 诊断 Agent | 模块接口尚未统一 | 待开发 | 使用 `prompts/diagnostic-agent.system.zh-CN.md` 和事件工具接入 |
| 策略进化 Agent | improvement/evolution 模块 | 部分落地 | 失败签名→候选策略→独立验证→全局晋升链 |
| 多工作区隔离 | session/workspace 字段分散存在 | 部分落地 | Schema 校验、跨工作区污染测试和水印强制门 |
| 全链路审计 | delivery audit、artifact、journal | 部分落地 | 统一 append-only event envelope、哈希链和回滚点 |
| 浏览器 MCP 深度融合 | 多种 transport 和 UI fallback | 待真实验收 | provider capability matrix、后台失焦、多标签和并发实测 |

## 当前真实状态

- Qoder CN、CodeBuddy CN 已有 AEP 文件派工 ACK 和分析产物；
- 国际版 Qoder、WorkBuddy、WorkBuddy AI 仍有 `WAKE_QUEUED_UNVERIFIED`，不能算完成；
- Batch 12 研究阶段已完成，实施阶段 ready，外部提交仍由额度和证据门控制；
- 现有 96 个测试文件/模块覆盖大量局部契约，但不等价于跨宿主、跨重启的生产验收。
