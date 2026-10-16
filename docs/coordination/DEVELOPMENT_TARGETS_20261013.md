# 多 Agent 协作机制：待开发目标与验收表（2026-10-13）

本表区分**制度已写、代码已实现、单元测试通过、实机端到端验收**。前三级不能自动推导第四级。基线：本机仅 Codex 原会话有 `MESSAGE_VISIBLE` 实测，尚无该事件的目标 Agent ACK；Qoder 一次紧急投递因适配器无输入控件而 `CEASED`。信箱文件数量不算送达率。任何能力升级均须保留原始事件 ID、失败回执和复现步骤。

2026-10-14 增量：未注册客户端、无窗口、窗口不唯一、启动被拒绝改为 `DEFERRED`；后台 tick 仅派发首次 `DEPOSITED` 订单，防止 `FAILED` 或窗口已定位但发送结果不明时重复输入。只有显式 `resume-deferred --order-id` 可重试确定未输入的订单，并保留原事件 ID。此改动通过单元测试，尚未证明六位队友的原会话送达/ACK；下一目标是客户端注册与归队事件触发的安全补投，以及 Qoder/WorkBuddy 的实机验收。

## P0：正确的人、正确的会话、确实接令

| 目标 | 实施方向 | 验收门槛 |
| --- | --- | --- |
| 客户端原生通道优先 | 每家先查官方 Hook/API/CLI/可归属的 CDP；文件信箱是耐久存储，UI 是兜底 | 至少 WorkBuddy、Qoder 各完成一次原会话无害投递、独立读回、目标回复挑战 ACK；不新建会话 |
| Electron UI 兜底 | 先绑定唯一 PID、窗口、会话标识和模型；再以可验证的输入框定位发送；模糊即停 | 窗口重复、输入框缺失、模型变化、UAC/跨权限、OCR 错字均不误发；未知写入不自动重试 |
| 投递证据账本 | 聚合 `DEPOSITED`、`MESSAGE_VISIBLE`、`AGENT_ACK`、任务认领、产物验证 | 每名 Agent、每个事件可追踪分母与状态；失联/没有采集能力不得记为已读或躺平 |
| 旧指令纠错 | 对已投递但未 ACK 的重要警示保留待办，归队时补读 | WSLC 记忆纠错逐人回报修改位置；没有 ACK 者明确显示未完成 |
| 文档时间漂移 | 文档含状态时间、证据与替代版本；过期事实显著标注 | `OCTO_INTEGRATION.md` 等旧“WSLC 必须提权”表述被纠正；新 Agent 入口不导向旧结论 |

## P1：事件驱动的协作和作战质量

| 目标 | 验收门槛 |
| --- | --- |
| 会话全文总线 | Codex、WorkBuddy、Qoder、CodeBuddy 各至少有一份可读且可归属原会话的增量记录；OCR 质量单独评分。静默时不轮询模型 |
| 自动归队与交接 | Agent 重启后定位原会话、看到未读摘要，显式 ACK 后恢复原任务；错误会话不得接管 |
| 同题会诊与创新 | 主攻、跨平台诊断、鲶鱼反证、独立审阅各有可复核产物；一轮失败能形成下一轮不同假设而非重复投包 |
| 回表停火与连续波次 | 同题同芯同根因连续两次失败自动冻结该路线；新包只有在修复证据、回归、SHA、额度与上传锁复核后才可投 |
| 编辑并发控制 | 对同一路径或同一高风险工件有短租约、冲突检测、交接记录；不覆盖别的 Agent 未提交修改 |

## P2：可扩展性与长期治理

- 为任意新 Agent 提供只读发现、能力声明、无害投递试验、原会话绑定和失败回退；不得用“已安装”代替“已验收”。
- OCTO 等实时会客厅只作可选通知扇出；本地事件与租约仍是权威状态。没有真实服务端和 Bot 权限验收时不称已接入。
- 命令经验库把 Windows 编码、转义、长路径 `subst`、WSLC 会话及跨平台编译案例转成可复用配方；每条配方要有真实成功证据，不靠聊天复述升格。
- 激励账本按证据、反证和信息增益计分；在线时长、消息数量不计成果。处罚规则先影子运行，防止诱导刷分或冒险上传。

## 每轮发布门槛

1. 代码测试通过，并在 PowerShell 7 下设置 `PYTHONIOENCODING=utf-8` 跑完整套件；另做目标客户端实机试验。测试绿不等于真实消息可达。
2. 输出投递矩阵：目标、原会话 ID、输入/发送/读回/ACK 各自证据与未知项；不把 `MESSAGE_VISIBLE` 说成接单。
3. 涉及真实上传、发布、删除、凭据或管理员入口，仍独立审查范围、幂等键和结果对账；失败不盲重试。
4. 只推送代码、测试与可公开文档；本地会话全文、信箱、令牌、个人 UI 档案和大镜像留在忽略目录。

下一轮优先顺序：**先可读的投递审计账本 → 再 WorkBuddy/Qoder 两条实机链路 → 再会话总线和作战自动闭环**。每突破一个客户端，记录一条端到端证据并更新能力矩阵，不用“全员已就绪”的总括措辞。

### 本轮进度

已加入只读 `delivery_audit.py`：

```powershell
$env:PYTHONPATH = 'K:\PythonProjects5\agent-evolution-protocol\src'
python -m agent_evolution_protocol.coordination.delivery_audit --root coordination --topic WSLC-MEMORY-FIX-20261013 --summary-only
```

2026-10-13 本机结果：7 条信箱消息，已读 0、原会话可见 0、Agent ACK 0、桌面送达停止 1、尚未尝试 6。该结果是**缺口证据**，不是对队友工作状态的评价。审计器只读，不启动客户端、不调用模型、不重试消息。下一步仍是逐客户端建立已验收原会话通道。

### 2026-10-14 进度复核

经代码核查，`src/agent_evolution_protocol/coordination/` 下已实现（代码层面）本表多项目标，须把"已实现代码"与"已实机验收"分开，避免再用"待开发"误导：

- **已实现代码、待逐客户端实机验收**：`commander.dispatch` 的 `profiled-ui` 全闭环（定位→发令→challenge-response 防自 ACK→读回 AGENT_REPLY_VERIFIED）；`commander.watch/tick` 无人值守编排（感知→查 ACK→重投→停火）；`ui_fallback.PowerShellUiaDriver` 同时支持 `uia` 与 `ocr` 两种 transport；`transcript_bus` + `commander.perceive` 会话全文总线；`legion_ledger` 积分/竞赛账本；`delivery_audit` 投递审计；`wake_daemon` 事件驱动队列+去重+冷却+审计。
- **已实机验收**：仅 Codex 原会话达成 `MESSAGE_VISIBLE`（PID 唯一、`input_verified=true`、独立读回事件 ID、`new_session=false`）；对方 ACK 因 10-13 额度耗尽未回，非机制问题。
- **主要短板（待锚定，不是机制缺失）**：WorkBuddy / Qoder / CodeBuddy 三家 Electron 系 UIA 无控件，`perceive` 返回 `NO_WINDOW` / `NOTHING_READABLE` / `WINDOW_NOT_UNIQUE`；需建 `ui_profiles/*.json` 并 `coord-client anchor` 钉唯一 PID 后才能走 OCR/键鼠投递。WorkBuddy 两个候选窗口未消歧前禁止发令（适配器已 fail-closed）。
- **机制缺口（真待开发）**：跨 agent 自动吵架/仲裁器未实现；竞赛"候选包→可投包"升级器缺回表/上传锁数据输入。
- **已实现代码（2026-10-14，待实机验收）**："换人/多波次"编排——`submit_order` 支持 `candidates`，`commander.reinforce` 在订单 `CEASED` 或久未 ACK 时自动生成差异化补位订单（火枪手轮次），单测 `tests/test_commander_reinforce.py` 已通过。

综合决策与升级路线见 `WARPLAN_UPGRADE_20261014.md`。

## 2026-10-17 补充：可迁移扩展工程

新增 [架构、理念与开发路线图](../PORTABLE_CAPABILITY_ROADMAP.zh-CN.md)：本轮实现隔离 SDK 安装、provider 来源版本清单和受限客户端配置导出。下一优先级是干净 Windows 验证的单 provider 安装器、原会话执行与 ACK 闭环；完整离线包和任意客户端兼容尚未完成。
