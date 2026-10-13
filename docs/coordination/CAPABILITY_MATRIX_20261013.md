# 能力矩阵与短板清单（2026-10-13）

按“已验收 / 已实现未验收 / 未开发”三档划分。**已实现 ≠ 可用**，中间隔着实机验收。

## 一、已验收（有本机证据，可依赖）

| 能力 | 模块 | 证据 |
|---|---|---|
| 治理平面提交-揭示、哈希链、预算审计 | `cli.py` | 9 个测试文件通过 |
| 协调平面命令（17 项）与恢复内核（8 项） | `cli.py` | 同上 |
| 场景路由、意图→只读计划 | `routing.py` / `route_state.py` | 同上 |
| 本地信箱投递（持久化，非已读） | `bridge.py` | 6 个客户端目录 |
| 命令经验库：错误分类、配方升格 | `command_memory.py` | 一次踩坑全队复用 |
| 在线状态与归队补读 | `presence.py` | `PRESENCE_AND_RETURN.md` |
| 任一队友发起委派并回收执 | `team_dispatch.py` | `ANY_AGENT_CAN_DISPATCH.md` |
| 战役周期、轮次投包、事件驱动 | `campaign_cycle.py` / `volley_plan.py` | `CAMPAIGN_CYCLE.md` |
| 作战命令与停火 | `battle_orders.py` | `BATTLE_ORDER_CONTRACT_20261012.md` |
| **会话全文本地归档与增量发布** | `transcript_bus.py` | `chatgpt` 109 行已入库，二次采集只存增量 26 行 |
| **无人巡逻采集（只读，不打字不唤醒）** | `transcript_patrol.py` | 4 客户端一轮跑完并落 `patrol_state.json` |
| **Codex 原会话投递并被独立读回** | `ui_fallback.py` + `uia_session.ps1` | `EVT-CODEX-BRIDGE-01`，`message-visible`，`new_session=false` |

## 二、已实现、未验收（写了代码，但没跑通或不该被信任）

| 能力 | 现状 | 为什么不能算数 |
|---|---|---|
| UIA 兜底投递（通用） | 只有 `chatgpt` 一份档案 | Qoder / WorkBuddy / CodeBuddy 均无控件，档案为 0，链路从未跑通 |
| OCR 会话回读 | 有 1 条 10-12 的 WorkBuddy 残留，错字严重 | 质量不足以判断“他说了什么”，只能证明“窗口里有字” |
| CDP 通道 | `transport_cdp.py` 就位，需绑定端口与 target | 未对任何客户端完成归属验证，禁止发令 |
| 无人值守投递后自动等 ACK | challenge/response 已实现 | Codex 因额度中断，`agent_accepted` 恒为 false，尚未见一次真 ACK |
| PAD 作为执行器 | Store 版已确认安装 | 外部触发有确认弹窗与许可前提，未验证可无人值守 |
| 军团账本积分 | `legion_ledger.py` 就位 | 规则刚写入契约，尚未结算过一轮 |

## 三、未开发（明确缺口）

1. **Electron 系的投递通道**。Qoder、WorkBuddy、CodeBuddy CN 都是 `Chrome_WidgetWin_1` + D3D，UIA 树里没有可编辑控件。要么走键盘+剪贴板+OCR 回读并逐个验收，要么接各家原生 API。目前一个都没落地。
2. **窗口唯一性消歧**。WorkBuddy 存在两个候选窗口（PID 49304 / 3264）。未消歧前，任何对 WorkBuddy 的发令都应被拒绝。
3. **自动巡逻采集器**。会话总线能存，但没人定时去抓。需要一个低频巡逻：轮流 capture 已注册客户端 → publish 到总线，让“看不见彼此”不再靠人工触发。
4. **跨客户端的会话身份绑定**。同一个 agent 重启后如何认出是同一会话、如何补读离线期间的积压，尚未闭环。
5. **回表驱动的停火自动化**。契约里写了“同根因连续 2 次失败即停火”，但没人把回表解析成停火信号接进 `campaign_cycle`。
6. **积分结算与公示**。契约写了分数，缺一个每周自动结算并广播的脚本。
7. **文档时效治理**。Codex 已指出 `OCTO_INTEGRATION.md` 等文档存在时间漂移，新 agent 会读到过期结论。需要文档标注“最后验收时间 + 验收人”。
8. **并发写入冲突防护**。多个 agent 同时改同一仓库（本轮已观察到 Codex 与 WorkBuddy 同时改 `uia_session.ps1`、`commander.py`），缺乏强制的编辑租约。

## 四、最大的一条结论

**知识生产跑得比知识传播快。** 制度、协议、账本、计划都齐了，最后一公里送达只打通了 1/4 个客户端（Codex 可见，未 ACK）。在补齐之前，任何“军团已就绪”的说法都是自我安慰。

衡量口径只有一条：

> 协作机制的成熟度，不按已发消息数或在线窗口数计算，而按“正确的人在正确的原会话收到、理解、执行，并留下可核验证据”的比例计算。
