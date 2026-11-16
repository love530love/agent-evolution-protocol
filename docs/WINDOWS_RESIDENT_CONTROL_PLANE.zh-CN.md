# Windows 常驻控制平面

## 结论

AEP 可以具备类似 Tailscale 的常驻能力，但必须拆成两个进程域。把它们混成一个
“高权限后台自动点桌面”的服务，会落入 Windows Session 0 隔离：服务可能健康，
却看不到用户登录会话里的 Qoder、WorkBuddy 或 CodeBuddy 窗口。

| 层 | 生命周期 | 责任 | 明确不做 |
|---|---|---|---|
| AEP Core Host | Windows 服务或登录后常驻 | 工作区、租约、订单、心跳、接力、审计、失败分类 | 聚焦窗口、输入、发送 |
| AEP Desktop Host | 每个交互用户会话一个登录任务 | 会话发现、严格原会话认证、桌面输入、消息可见与回读 | 自行规划、绕过订单、创建新会话 |
| Elevated Desktop Worker | 按订单短暂启动 | 对高完整性目标执行一个已批准订单 | 轮询队列、启动客户端、重放不确定投递 |

弱 Agent 只写入工作区事件或订单；它不需要安装 UIA、MCP、截图模型或拥有桌面权限。
中央调度器根据订单的会话绑定、完整性等级和执行器能力，将同一事件交给合格的
Desktop Host。执行器回传的是分层事实，而不是笼统的“成功”。

```text
Agent intent -> durable order -> Core Host lease -> Desktop Host
  -> SESSION_LOCATED -> INPUT_SUBMITTED -> MESSAGE_VISIBLE
  -> AGENT_ACK -> TASK_RESULT_READBACK
```

## 已有落地部件

- `coordination.core_host`：只处理持久状态与 DAG 的单实例常驻循环；它
  强制 `dispatch=False`，没有任何 GUI 调用路径；
- `coordination.supervisor`：Desktop Host 的单实例锁、持久心跳、异常不退出
  的有界 GUI tick；
- `wake_daemon`：事件队列、每事件锁、去重、冷却、审计；
- `scripts/aep-supervisor.ps1`：PowerShell 7 登录会话包装；
- `scripts/register-supervisor-task.ps1`：注册 `AEP-DesktopHost` 登录任务；
- `scripts/run-elevated-desktop-order.ps1` 与
  `scripts/start-elevated-desktop-order-task.ps1`：高完整性精确订单执行链。

Desktop Host 的默认协作根统一为仓库的 `coordination`，而不是历史遗留的
`.agent-evolution\\coordination`。每个 Host 的单实例锁和心跳必须以该根为边界，
从而避免同机多个 Agent 宿主相互拉起重复 MCP/stdio 进程。

## 推荐部署顺序

1. 先运行 `scripts/aep-core-host.ps1 -MaxTicks 1`，核验
   `runtime/aep-core-host.heartbeat.json` 的 `ui_touched=false`；它可随后由
   开机任务或 Windows 服务托管。
2. 再以登录任务部署 Desktop Host，并核验 `runtime/aep-supervisor.heartbeat.json`。
3. 为需要提升权限的客户端注册单订单 Elevated Worker；先执行只读 probe，再执行
   同一事件的 dispatch。

Core Host 与 Desktop Host 绝不能共同持有同一把锁，且 Core Host 不得启动每个
Agent 私有的 MCP/stdio 服务器。它只持久化订单，并由已经登记、健康且处于登录态的
Desktop Host 领取；这是避免多宿主重复拉起 166 个 MCP 进程的边界。

使用 `scripts/aep-resident-status.ps1` 检查两类心跳。只有 Core 与 Desktop
同时新鲜时，报告才会显示 `control_plane=READY`；即使如此，`delivery_claim` 仍为
`NONE`，直到某张订单留下原会话可见消息、目标 ACK 与结果回读证据。

部署初期可运行 `scripts/aep-supervisor.ps1 -ObserveOnly`。该模式只读取已经进入
协作区的同事件 ACK，不会发现窗口、写入输入框或派发订单；状态报告会显示
`OBSERVE_ONLY`，而不是错误地把它称为可发送的 `READY`。

任何一层重启都不得把 `SENDING`、`UNCERTAIN` 或未读回的旧订单重新输入。恢复时
必须先读取同一事件的会话回执，再决定继续、接力或 HOLD。
