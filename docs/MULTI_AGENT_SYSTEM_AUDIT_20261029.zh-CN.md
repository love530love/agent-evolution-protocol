# 多 Agent 协作机制专家团与用户团审计

## 总体结论

项目在协作协议、唤醒、会话绑定、能力路由、Computer-use 分层、证据审计、模型兜底和任务图方面已经形成完整骨架，明显超过普通 Agent 编排器。

当前最重要的事实是：**规划与治理层成熟，真实桌面执行与自治恢复层仍需工程化闭环。** 不应把工具发现、消息写入或计划生成误认为任务完成。

## 三方共识

### 架构专家

- 需要持久化 mission graph、事件日志和崩溃恢复；
- `delegated-open` 必须有统一审计记录，而不仅是计划字段；
- provider 需要任务级验收，不能只验证 MCP tool discovery；
- 能力清单应加入成功率、延迟、负载、失败签名和证据质量；
- 版本和 schema 已应进入 0.4.x 迁移周期。

### 自动化专家

- `commander.watch` 仍是有界轮询，应逐步接入文件变更、SSE、事件游标或 watchdog；
- MCP 当前以发现/schema 探测为主，实际调用和统一 readback 尚未完全闭环；
- Agent 下线、心跳过期、租约过期后的自动 reassign 需要独立事件机制；
- 回表监控需要去重、退避和额度预算；
- mission graph 需要连接 wake、commander、evidence，使后继节点自动推进。

### 用户与新 Agent 代表

- README 首屏需要“一条命令加入、一句自然语言任务、一个成功输出”；
- 应提供 `aep doctor --onboarding`，统一诊断 Windows、Python、模型、CUDA、Git LFS、长路径、权限和桌面 provider；
- 应提供统一 `aep mission status`，一次显示阶段、负责人、下一动作、证据缺口和阻塞原因；
- 错误应映射为下一步可执行建议，而不是只展示原始 traceback；
- 核心协议、模型、桌面 provider、MCP 应分层安装。

## 优先级路线

### P0：真实闭环

1. 选定一个 CDP 或 UIA provider，完成“发现原窗口 → 发送 → readback → ACK”的端到端验收；
2. 将 mission graph 持久化并接入 wake/commander/evidence；
3. 增加统一 provider readback contract；
4. 增加 `aep doctor` 和统一任务状态入口。

### P1：自治调度

1. 事件驱动监督、心跳租约和自动 reassign；
2. 能力评分闭环；
3. 回表 bounded-backoff、签名去重和额度预算；
4. MCP 实际调用适配器。

### P2：自我进化

1. 动作录制与离线重放；
2. 故障注入和数字孪生演练；
3. 经验卡自动蒸馏与失效检测；
4. 0.4.x schema migration 与跨平台 acceptance matrix。

## 验收标准

- 新 Agent 5 分钟内加入并读懂当前任务；
- 弱 Agent 用一句自然语言找到具备能力的执行者；
- 真实电脑任务有前后状态、证据、ACK 和可恢复状态；
- 模型、桌面 provider 或单个 Agent 缺失时仍能文件接力；
- Windows 干净环境可一键诊断并明确列出缺失项；
- 任何失败都产生下一步动作，而不是静默中断。
