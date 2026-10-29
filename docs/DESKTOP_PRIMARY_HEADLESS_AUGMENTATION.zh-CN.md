# Desktop-Primary / Headless-Augmentation 生产架构

## 结论

对于异构 Agent，最稳妥的方案不是把所有能力押在无头 API 上，也不是只靠坐标点击。
采用双平面：

```text
                    ┌──────────────────────────────┐
                    │ AEP Workspace / Supervisor    │
                    │ 状态、租约、事件、接力、ACK    │
                    └──────────────┬───────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              │                                         │
   ┌──────────▼──────────┐                    ┌─────────▼─────────┐
   │ 桌面主控平面          │                    │ 无头增强平面        │
   │ 启动、窗口、UIA、回读 │                    │ API/CLI/MCP/CDP/A2A│
   └──────────┬──────────┘                    └─────────┬─────────┘
              │                                         │
              └──────────────┬──────────────────────────┘
                             │
                    同一 Agent Session Anchor
```

桌面平面是身份和恢复锚点；无头平面是速度、后台执行和长任务补强。两个平面不能各自维护
一份会话状态，也不能并发重复发送同一个事件。

## 一、启动方案：用户会话内的 Supervisor + Desktop Runner

### Supervisor

Supervisor 可以作为服务、计划任务或常驻进程运行，负责：

- 读取 Workspace 快照；
- 选择 Agent profile；
- 检查进程、用户 SID、Windows session id；
- 维护事件、租约、超时和接力；
- 优先调用已验收的无头通道；
- 无头不可用时，把动作交给 Desktop Runner。

### Desktop Runner

Desktop Runner 必须运行在目标用户的交互式 Windows 会话，而不是 Session 0 服务桌面。
它负责：

- 启动 Agent GUI；
- 等待 UIA 根节点/窗口/输入框就绪；
- 持有桌面输入租约；
- 通过控件名、AutomationId、RuntimeId 定位；
- 读回界面状态并产出证据；
- 将 UIA 证据回传给 Supervisor。

微软文档说明，Windows 服务不能直接和用户桌面交互，服务运行在 Session 0；需要在用户交互会话中
创建辅助 GUI 进程。[Interactive Services](https://learn.microsoft.com/en-us/windows/desktop/Services/interactive-services)

因此，GUI Agent 的无人值守方案应采用“服务 Supervisor + 登录用户 Desktop Runner”，而不是让
LocalSystem 服务直接操作 GUI。

## 二、会话定位：五级身份锚定

发现结果必须生成 `session_anchor`，从强到弱依次为：

1. 原生 `conversation_id` / `session_id`；
2. MCP/A2A/API 返回的 session identifier；
3. CDP `target_id` + URL + 所属浏览器 PID；
4. UIA AutomationId/RuntimeId + PID + 进程创建时间；
5. 进程路径哈希 + 用户 SID + Windows session id + 窗口标题 + UIA 树指纹。

坐标、OCR 文本和窗口标题只能作为辅助证据，不能独立作为生产会话身份。

定位规则：

- 唯一候选：可以绑定；
- 多个候选：必须要求已存在 anchor 或返回 `SESSION_BINDING_REQUIRED`；
- 没有候选：可以按授权启动程序，再重新发现；
- 进程重启：PID 失效，必须重新发现并重新计算指纹；
- 会话 ID 改变：旧会话只读归档，新会话必须重新完成接管验收。

## 三、通道选择：桌面为基准，无头为增强

### 启动阶段

始终由 Desktop Runner 或已登记的原生 launcher 启动，并记录：

```json
{
  "agent": "qoder_cn",
  "pid": 1234,
  "process_start_time": "...",
  "exe_sha256": "...",
  "user_sid": "...",
  "windows_session_id": 2,
  "window_handle": 123456,
  "workspace_id": "W-..."
}
```

### 能力发现阶段

在桌面身份锚定后，探测同一 Agent 是否有：

- 原生 API；
- CLI；
- MCP Server；
- WebSocket/HTTP 本地端口；
- Electron/Chromium CDP；
- ClawChat 或其他消息桥。

探测成功只记录能力，不自动把它当成已接入。必须用同一个 `session_anchor` 做一次最小派工验收。

### 派工阶段

推荐顺序：

```text
已验收 native API/MCP/A2A
        ↓ 不可用
已验收 CDP
        ↓ 不可用
UIA 控件级桌面操作
        ↓ UIA 不可读
OCR/键盘/剪贴板最后回退
```

桌面主控并不意味着每条消息都必须点击 GUI，而是确保任何无头通道失效时，仍可通过真实窗口
恢复和定位，不会陷入“后台命令执行成功但 Agent 根本没有收到”的假成功。

## 四、发送闭环

每一次派工都必须按以下状态机推进：

```text
SESSION_LOCATED
  → LEASE_ACQUIRED
  → INPUT_SUBMITTED
  → MESSAGE_VISIBLE
  → AGENT_ACK
  → RESULT_READBACK
  → EVIDENCE_VERIFIED
```

关键规则：

- `INPUT_SUBMITTED` 不等于 `MESSAGE_VISIBLE`；
- `MESSAGE_VISIBLE` 不等于 `AGENT_ACK`；
- `UNKNOWN` 状态禁止自动换通道重复发送；
- 只有确认“尚未输入”的 `DEFERRED` 才能重试；
- 同一事件 ID 只能有一个活动发送租约；
- 无头和桌面通道不得同时发送同一事件。

## 五、无人值守模式

### 用户已登录

```text
Supervisor 常驻
Desktop Runner 在用户 Session 中常驻
Agent GUI 可被启动、定位和恢复
无头通道可随时增强
```

这是 Windows GUI Agent 的首选无人值守模式。

### 用户注销或锁屏

- 无头 API/MCP/CDP 可以继续工作，则继续；
- GUI Runner 标记 `DESKTOP_SESSION_UNAVAILABLE`；
- 不把 GUI 未操作伪装成完成；
- 等待用户会话恢复，或把任务接力给无头 Agent；
- 如果必须操控 GUI，使用持久 VM/RDP 用户会话，而不是把服务硬塞进 Session 0。

## 六、故障和回退

| 故障 | 动作 |
|---|---|
| Agent 未启动 | Desktop Runner 启动并等待就绪信号 |
| 窗口存在但不唯一 | 读取 anchor；没有 anchor 就阻塞 |
| UIA 不可读 | 尝试 CDP/native；否则进入人工确认 |
| 无头端口失效 | 回退到 UIA，不重复发送未确认事件 |
| GUI 锁屏不可用 | 等待用户 Session 或接力给无头 Agent |
| Agent 进程重启 | 重建指纹和 session anchor |
| 输入结果未知 | 冻结事件，先读回，不盲目回车 |
| Agent 无 ACK | 进入诊断/接管流程，不把已读当接单 |

## 七、AEP 当前对应关系

- `coord-client launch`：程序启动；
- `client_registry.discover`：窗口/进程发现；
- `client_registry.set_anchor`：原会话锚定；
- `coord-agent-mcp render-config`：生成无头增强配置；
- `aep-mcp-server`：联邦控制面；
- `commander`：持久派工和发送状态机；
- `ui_fallback` / UIA MCP：桌面回退；
- `transport_cdp`：浏览器/Electron 无头通道；
- `workspace_identity`：工作区水印和跨工作区隔离。

### 可执行入口

```powershell
# 只预览，不启动程序
python -m agent_evolution_protocol --coord-root .\coordination `
  coord-desktop-runner launch --agent qoder_cn

# 明确授权后，在用户 Session 启动并等待可观察就绪
python -m agent_evolution_protocol --coord-root .\coordination `
  coord-desktop-runner ensure --agent qoder_cn --apply --allow-launch

# Supervisor/Runner 持久 IPC
python -m agent_evolution_protocol --coord-root .\coordination `
  coord-desktop-ipc recover --workspace-id W-0123456789abcdef01234567 `
  --agent qoder_cn
```

`coord-desktop-runner` 默认只预览；`--apply --allow-launch` 才会请求启动已登记的程序。
启动本身仍不是派工成功，必须继续执行会话绑定、输入、回读和 ACK 验收。

## 最终判断

这套方案可以覆盖“能启动、能定位、能派工、能回读、能接力”的主要路径，但不能凭空制造
厂商没有提供的后台 API。对于没有无头接口的 Agent，桌面 Runner 是可靠主路径；对于暴露 API、
MCP、CDP 或 A2A 的 Agent，无头模式是同一会话的增强通道。

生产验收必须以每个 Agent 的真实事件闭环为准，而不是仅凭进程已启动或 MCP 工具已发现。
