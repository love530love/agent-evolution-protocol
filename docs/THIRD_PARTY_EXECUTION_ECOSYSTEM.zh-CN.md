# 第三方执行生态接入策略

## 结论

这批项目可以显著增强 AEP 的“手脚”，但不应直接替换 AEP 内核。正确的集成方式是：

```text
第三方项目 = 专业执行能力
AEP       = 工作区、会话、租约、派工、证据、接管和进化内核
```

第三方项目不论多强，都必须经过 AEP 的 `WorkspaceID + session anchor + event_id + ACK` 验收，不能因为工具返回 200、窗口出现或动作执行就宣布 Agent 已接单。

## 能力分层

| 层 | 首选候选 | 接入形式 | AEP 角色 |
|---|---|---|---|
| 浏览器 | Browser Use、Skyvern、LaVague | Skill + Connector | 浏览器标签/会话、DOM/视觉动作、回读 |
| Windows 桌面 | UI-TARS、UFO2、Agent S2 | App + Connector | GUI 规划、截图、控件/视觉动作 |
| 终端执行 | Open Interpreter | Skill + Connector | Shell、文件、代码执行、artifact 回读 |
| 工程 Agent | OpenHands | App + Connector | 长程代码任务、DAG、checkpoint、handoff |
| 演示回放 | OpenAdapt | Skill + Connector | 把一次人工操作固化为可回放流程 |
| 移动端 | Appium MCP | MCP + Connector | 真机/模拟器会话和测试产物 |
| 逆向取证 | REA | MCP/CLI Connector | 错误归因、运行行为、二进制/协议探针 |

完整候选登记在 `examples/execution-provider-catalog.json`。该文件只是候选目录，不代表已经安装、联网或信任。

## 四级接入标准

### L0：目录登记

记录仓库、运行时、许可证、适用场景、限制和预计连接器。此阶段不安装。

### L1：能力探测

在隔离环境运行最小探针：版本、工具列表、依赖、服务端健康状态。只证明“工具可用”，不证明“能控制正确会话”。

### L2：会话验收

绑定唯一的窗口/PID、浏览器 target、设备 ID 或原生 conversation ID，执行无副作用的 inspect/readback。没有稳定 anchor 就返回 `SESSION_BINDING_REQUIRED`。

### L3：生产派工

使用 `aep-dispatch-envelope-v1`，通过能力包的 skill/connector 执行一次真实派工；必须取得 `MESSAGE_VISIBLE → AGENT_ACK → RESULT_VERIFIED` 证据链。

## 对 AEP 的具体补强

### Browser Use / Skyvern / LaVague

- 作为浏览器 Connector，而不是新的调度内核；
- Browser Use 适合通用 DOM/Playwright 任务；
- Skyvern 适合长流程表单和持久浏览器会话；
- LaVague 适合规格驱动的测试和 World Model/Action Engine 分离；
- 所有浏览器操作必须绑定 tab/target，禁止只凭 URL 猜窗口。

### UI-TARS / UFO2 / Agent S2 / Self-Operating Computer

- 作为视觉桌面 Provider，补足 UIA 不可读时的屏幕理解；
- 视觉动作必须输出截图、动作序列和目标窗口证据；
- 不能把“模型说已点击”当成点击事实；
- AEP 的桌面 lease 负责避免多个 Agent 同时操控同一桌面。

### Open Interpreter / OpenHands

- Open Interpreter 作为本地终端/文件执行器；
- OpenHands 作为代码工程长程执行器和备用 Agent 群；
- 命令执行必须回传命令、退出码、工作区 diff、产物 checksum；
- 不能绕过 AEP 的 workspace、预算和 handoff 机制。

### OpenAdapt

- 适合把已验收的重复 GUI 流程固化成低成本回放；
- 回放程序仍需检查窗口锚点和外部状态，不能在错误会话中盲放。

### Appium MCP

- 把移动设备和模拟器纳入同一套 Workspace/Session/ACK 体系；
- 设备 ID、应用包名、测试报告和截图必须进入 artifact manifest。

## 推荐落地顺序

1. **Open Interpreter Connector**：先补本地命令、文件和跨平台编译手脚；
2. **Browser Use Connector**：验证浏览器标签/会话定位和回读；
3. **UI-TARS/UFO2 Provider**：作为 UIA/OCR 的视觉备用层；
4. **OpenHands Connector**：接入长程工程任务和 checkpoint；
5. **OpenAdapt Skill**：沉淀高频稳定流程；
6. **Appium MCP**：扩展到移动端；
7. **Skyvern/LaVague/Agent S2**：按真实任务需要选择，不盲目全量安装。

## 当前状态与边界

- AEP 已有 Capability Bundle v1，可登记 `servers + skills + connectors + observability`；
- 已有 Qoder Bundle 示例和统一派工契约；
- 本目录中的第三方项目目前是候选能力，不代表本机已全部安装或完成生产验收；
- 每个候选必须经过 L0→L3，才能进入默认路由；
- 红皇后机制负责发现新能力，鲶鱼机制负责挑战“是否真的可用”，创造性破坏机制负责淘汰无法通过真实验收的 Provider。
