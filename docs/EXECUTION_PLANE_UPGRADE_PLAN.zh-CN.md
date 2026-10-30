# 执行平面升级计划：从“工具发现”到“可验证接管”

## 结论先行

这次 Qoder 派工失败确实暴露了项目短板，也暴露了我的判断短板：我们曾经把“发现了 MCP/打开了窗口/输入框里出现文字”误当成“可以稳定执行”。这三者都不是生产成功条件。

当前项目已经具备工作区、身份锚定、事件、信箱、ACK 契约和恢复策略；缺口集中在执行平面：如何在不同厂商的 Electron/浏览器/桌面程序中，**精确绑定原会话、选择有效输入通道、提交动作、回读副作用、取得原 Agent 回执，并在失败时切换通道而不重复投递**。

本文件把用户列出的开源项目纳入统一的能力地图，但不把“上游项目存在”冒充为“本机已接入”。

## 1. 已确认的根因

| 现象 | 真实含义 | 处置 |
|---|---|---|
| Qoder 窗口可见 | 只能证明进程和窗口存在 | 仍需 PID/标题/工作区/会话四元锚定 |
| UIA 只能读到根窗口，找不到 composer | Electron/D3D 渲染层未暴露可用控件树 | 切到 ACP/SDK/CDP/视觉通道；禁止盲点 |
| CUA 点击或输入没有改变页面 | 当前桌面输入通道没有产生可观测副作用 | 记为 `INPUT_CHANNEL_UNAVAILABLE`，不得报告成功 |
| 输入框曾显示文本 | 只代表 `INPUT_SUBMITTED` | 必须继续验证 `MESSAGE_VISIBLE` 和 `AGENT_ACK` |
| MCP `tools/list` 成功 | 只代表服务器能提供工具 | 还没有证明能定位正确会话或发送 |

因此，当前第三方目录全部保持 `candidate` 是正确状态；它们尚未经过同一原会话的 L2/L3 验收。

## 2. 能力地图与推荐优先级

| 优先级 | 能力 | 首选候选 | 接入方式 | 生产验收关键 |
|---|---|---|---|---|
| P0 | 本地命令、文件、编译、产物校验 | Open Interpreter | Connector/Skill | 命令、退出码、diff、checksum |
| P0 | 浏览器 DOM/CDP 会话 | Browser Use + Playwright | Browser Connector | target id、同标签回读、事件 ACK |
| P0 | Electron/桌面原生会话 | Qoder ACP/SDK、UI-TARS、UFO2 | 原生适配器 + 视觉备用 | 会话 ID、动作证据、回读、ACK |
| P1 | 长程工程任务与接力 | OpenHands | App/Connector | checkpoint、工件清单、测试、handoff |
| P1 | 已成功流程的确定性回放 | OpenAdapt | Skill | 录制来源、窗口锚点、回放校验、异常回退 |
| P2 | 复杂浏览器表单 | Skyvern、LaVague | Browser Connector | 会话池、动作轨迹、恢复点 |
| P2 | 分层视觉规划/基准对比 | Agent S2、Self-Operating Computer | Visual Provider | 截图、动作序列、负路径测试 |
| P3 | 移动端 | Appium MCP | MCP Connector | 设备 ID、应用状态、日志、报告 |
| P0 | 逆向取证和故障归因 | REA | MCP/CLI Connector | 目标身份、探针日志、复现脚本、哈希 |

## 3. 统一接入契约

任何 Provider 必须提供以下六个适配器动作：

```text
probe()       -> 版本、工具、依赖、健康状态
observe()     -> 当前窗口/标签/设备/会话证据
focus()       -> 取得目标会话租约，不发送副作用
compose()     -> 在指定会话构造消息/命令
send()        -> 产生带 event_id 的动作
readback()    -> 回读 MESSAGE_VISIBLE / RESULT / AGENT_ACK
```

执行状态严格按以下顺序升级：

```text
candidate
  -> probed
  -> session-accepted
  -> task-tested
  -> production
```

`tools/list`、进程启动、窗口截图、输入框文字都不能跳过中间状态。只有 `task-tested` 且拿到当前事件的完整证据链，才允许进入生产派工候选。

本规则已固化在 `coordination/execution_plane.py`：

- `classify_observation()` 将 Qoder 当前失败归类为稳定错误码；
- `DeliveryEvidence` 强制校验 `session -> input -> visible -> ACK`；
- `route_candidates()` 排除 candidate/probed Provider，不会把“发现成功”路由为“生产可用”。

## 4. 路由原则

1. **Headless-first**：已有官方/稳定 ACP、SDK、CDP、浏览器 target 时优先无头/协议通道。
2. **Desktop-augmentation**：协议通道不能触达时，再用 UIA、视觉、OCR、输入注入；每一步必须回读。
3. **Deterministic-replay**：同一流程成功一次后，优先使用 OpenAdapt 类回放，减少模型调用和坐标漂移。
4. **No blind fallback**：切换通道前冻结原事件；无 `MESSAGE_VISIBLE` 或 `AGENT_ACK` 时不能重复投递。
5. **Provider quarantine**：同一 Provider 连续两次出现“输入成功但无发送回读”，自动降级为 `session-accepted` 以下，交给 REA/诊断 Agent 分析。

## 5. 本轮开发目标

### 已完成

- Qoder 国际版与 Qoder CN 已分离为不同 anchor、PID 和可执行路径；
- 现有桌面 MCP 已完成工具发现，但保持 reference-only；
- 已加入执行平面证据模型和路由测试；
- 当前回归测试覆盖 41 项，包含桌面发现、派工安全和 UI fallback。

### 下一阶段（按顺序）

1. 实现 Qoder ACP/SDK 探测器：检查 SDK daemon、命名管道、WebSocket/本地端口；只有发现可用会话 API 才启用协议通道。
2. 为 Browser Use/Playwright 增加真实 target 绑定和无副作用 `observe/readback` 验收。
3. 为 UI-TARS/UFO2 增加截图—动作—截图的证据适配器，并接入桌面输入租约。
4. 建立 provider conformance harness：同一套 synthetic task 验证所有 Provider 的六个动作和失败分类。
5. 将 Open Interpreter/OpenHands 的工件校验接入 `artifact_manifest`，把命令执行和代码任务纳入同一 Workspace DAG。
6. 成功流程通过后才生成 OpenAdapt 回放；回放也必须检查会话锚点。

> 打包注意：当前 `coordination/ui_profiles/` 和窗口/PID 锚点属于本机运行态，按 `.gitignore` 保持不上传。发布前必须把不含 PID、token 和个人会话文本的模板移入 `examples/`，由安装器在目标机器重新探测并生成锚点；否则“源码可复现”仍不等于“本机可接管”。

## 6. 生产发布门槛

发布或默认启用前必须同时满足：

- 新鲜进程/新鲜工作区测试通过；
- 至少一次原会话真实派工；
- 有唯一 `WorkspaceID + session_id + event_id`；
- 存在 `MESSAGE_VISIBLE` 和 `AGENT_ACK` 证据；
- 失败时能产生可复现的错误分类；
- 重启后可从快照恢复，不产生重复事件；
- 上游许可证、依赖和归属已记录，未验证项目仍是 reference-only。

## 7. 对本次短板的明确承诺

从现在开始，项目报告只使用四种事实标签：`DISCOVERED`、`PROBED`、`SESSION_ACCEPTED`、`TASK_TESTED`。任何没有回读和 ACK 的动作都只能报告为“尝试/未确认”，绝不再报告为完成。
