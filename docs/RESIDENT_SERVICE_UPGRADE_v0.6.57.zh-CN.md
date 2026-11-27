# AEP 常驻服务升级 v0.6.57：从“计划任务能启动”到“可核验的持续值守”

状态：**实现中的 v0.6.57 设计与第一阶段代码**。本文不把代码存在、计划任务显示
`Running`、窗口打开或信箱落盘表述为真实派工成功。

## 1. 这次为什么必须升级

截至 2026-11-27 的实机读数中：

- `AEP-CoreHost` 有新鲜心跳，但它以 `SYSTEM` 身份运行，普通用户无法读取其进程创建时间，
  因而 `resident-status` 正确给出 `PROCESS_UNKNOWN`，控制面仍为 `HOLD`；
- `AEP-DesktopHost` 是交互用户任务，当前为 `observe-only`，它能证明读取 ACK 的循环活着，
  **不能**证明输入、发送、ACK 或任务结果；
- 单独的隔离任务故障注入曾得到“子进程退出、任务回到 Ready、超过一分钟没有新心跳”。
  因此 Task Scheduler XML 中的 `RestartOnFailure` 不可被当作已验收的自愈能力；
- 全机 MCP/终端进程不能靠名称批量清理。那会误伤 Qoder、WorkBuddy、Codex 等宿主自己的
  stdio 服务，也不能证明任何重启是正确的。

结论：计划任务只应负责**在登录或开机时引导**；进程健康、重启预算和证据必须由 AEP
自身的持久化状态机负责。

## 2. 目标结构

```text
Windows SCM / 开机引导
        │
        ▼
┌──────────────────────────────────┐
│ AEP Core Host（SYSTEM，永不碰 UI） │
│ 任务图、订单、租约、审计、恢复投影 │
└──────────────┬───────────────────┘
               │ durable journal / future local IPC hint
               ▼
┌──────────────────────────────────┐  用户登录会话
│ AEP Desktop Guardian（每用户一个）│  ← 计划任务只负责启动它
│ 自有锁、重启预算、父子身份、心跳  │
└──────────────┬───────────────────┘
               │ owns exactly one Popen child
               ▼
┌──────────────────────────────────┐
│ AEP Desktop Host / Supervisor     │
│ 原会话定位、输入、发送、回读 ACK  │
│ 仅在严格会话证明+订单授权后触 UI  │
└──────────────────────────────────┘
```

Core 的职责是“任务不会因某个厂商额度或某个前台程序退出而丢失”；Desktop Host 的职责才是
“在**已验明的原会话**里操作”。二者不能混为一个权限很大的后台进程。

## 3. 第一阶段：已落地的 Guardian 组件

新增 `coordination.resident_guardian` 及 `scripts/aep-desktop-guardian.ps1`：

1. Guardian 以 Python 常驻父进程运行，计划任务改为通过
   `aep-desktop-guardian-runner.py` **直接启动基础 Python**，而非通过 venv redirector
   再转启动 Supervisor；这样停止/替换任务能终止实际 Guardian，由其关闭 Job 回收自有 Host；
2. 它仅保存并控制**自己 `Popen` 创建的那个** Desktop Host 子进程；不会扫描或结束同名
   `python.exe`、`node.exe`、`conhost.exe`，也不会碰厂商客户端的 MCP；
3. 它写两份可恢复记录：
   `runtime/aep-desktop-guardian.heartbeat.json` 与
   `runtime/aep-desktop-guardian.state.json`。记录 run id、子 PID、重启窗口计数、退避截止
   时间、模式和原因，且 `delivery_claim=NONE`；
4. 子进程退出、心跳丢失、错误 PID 或陈旧心跳触发**只针对该直接子进程**的恢复；
   5 次/10 分钟默认预算耗尽后进入 `RESTART_BACKOFF`，继续写心跳而不是无限拉起进程；
5. 在 Windows 上，Guardian 在启动 Desktop Host 前创建唯一随机命名的 `AEPResident-*`
   Job Object，并按**它刚刚创建的 PID**加入；Job Object 带 `KILL_ON_JOB_CLOSE`，因此
   Guardian 退出时会回收该 Host 及其正常子树。Job 创建/绑定失败时不启动 Host，记录
   `CONTAINMENT_UNAVAILABLE`，而不是悄悄留下孤儿进程；
6. `resident-status` 新增 `recovery_assurance`：
   `SUPERVISED` / `LEGACY_UNSUPERVISED` /
   `GUARDIAN_UNHEALTHY` / `GUARDIAN_CHILD_MISMATCH` /
   `GUARDIAN_UNCONTAINED`。后面三种强制控制面 `HOLD`。
7. Guardian 管理的 Host 若发现已有旧 Supervisor 持有同一协调根的互斥锁，会以临时失败
   `75` 返回，Guardian 记录 `WORKER_LOCK_HELD/SUPERVISOR_ALREADY_RUNNING`，而不会把它
   伪装成正常 `EXIT_0`；它不会擅自按名称杀掉外部 owner。
8. dispatch 模式的 `--local-queue` 现在由 Guardian 原样传给自有 Host；observe-only
   与本地执行队列的组合会被拒绝，避免“只读值守”暗中变为本地执行。

当前源码已把 `register-supervisor-task.ps1` 的**下一次注册**入口改为 Guardian，但没有把
“源码已改”说成“本机任务已迁移”。迁移仍需独立、可回滚的实机验收。

### 第一阶段隔离实机证据（2026-11-27）

两次 `observe-only` 临时协调目录演练均未触碰厂商窗口或发送消息：

1. Guardian 启动真实 Supervisor 后，`resident-status` 读到 Desktop 与 Guardian 都是
   `HEALTHY`，`recovery_assurance=SUPERVISED`，两侧 child PID 相同；
2. 明确核对临时 Supervisor 的 PID、创建时间与命令行中的临时协调根后，只终止该一个
   子进程。Guardian 生成新的 PID，重启窗口计数从 0 变为 1，随后再次读到
   `SUPERVISED`。

演练还发现并修复了两项会在生产中形成假阳性的缺陷：虚拟环境 `Scripts\\python.exe`
会二次拉起基础解释器（Guardian 曾拿到 redirector PID），现改为显式使用
`sys._base_executable` 并保留 venv site-packages；状态读取也改为先读原子心跳、后取时钟，
避免刚写入的微秒级心跳被误判为“来自未来”。这两项证据仍只证明**进程恢复**，不证明真实
原会话投递。

第三次独立演练验证了 Guardian 自身异常退出的边界：临时 observe-only Guardian 的
heartbeat 先报告 `child_containment=WINDOWS_JOB`，随后只终止该 Guardian 的精确 PID。
其唯一 Desktop Host child 立即由 Job Object 回收（读取进程状态为已退出）。此演练没有
触碰任何现有 AEP 任务或厂商客户端；它证明的是 **G1 的父崩溃回收**，仍不构成 G5 消息投递。

第四次是本机 `AEP-DesktopHost` 的任务级验收：先把任务 XML 改为基础
`python.exe → aep-desktop-guardian-runner.py`，再核验 Guardian 与其 Host 的 PID、创建时间、
命令行、会话根以及 `WINDOWS_JOB` 关联。执行 `schtasks /End` 后，这一对经过核验的 PID 都已
退出；随后 `schtasks /Run` 产生一对不同的新 PID，并重新读到
`SUPERVISED + WINDOWS_JOB + observe-only`。这证明**任务停止/重启会受控回收 AEP 自有子树**；
Core 身份尚无法由普通用户验证，所以整体控制面仍正确保持 `HOLD`。

第五次是已部署 Guardian 的 child-recovery 演练：只终止已经由 PID+创建时间+命令行+
`--guardian-managed`+协调根共同核验的 observe-only Host。保持运行的同一 Guardian 在一个
有界周期内生成不同的新 Host PID，`restart_count_window` 从 0 变为 1，读回仍为
`SUPERVISED + WINDOWS_JOB + observe-only`。这证明的是 **G2 的自有 Host 恢复**，没有输入、
发送或读取任意厂商会话。

## 4. 后续两个阶段

### 第二阶段：Core 变为真正的 Windows 服务

把 Core 包成最小的 `AEP-Core` Windows Service（SCM 负责开机和服务级恢复），但仍只做：

- SQLite/JSON 审计投影、DAG、租约、容量/额度接力、死信、订单恢复；
- 本地认证 IPC 的“有新订单/需重新检查”提示；
- 主动状态查询与 `WAITING_FOR_INTERACTIVE_SESSION` 告警。

它不会从 Session 0 模拟鼠标键盘，也不会以管理员权限直接向任意厂商窗口注入文本。用户注销时
Desktop Guardian 应明确报告 `NO_INTERACTIVE_SESSION`，而不是伪造“发送失败”或反复启动前台程序。

### 第三阶段：可靠实时提示，但不取代耐久账本

命名管道或本机 loopback WebSocket 可以用于降低延迟（Core 通知 Guardian 有订单、Guardian
立即重新读取订单），但它只能是**提示通道**。权威状态仍是带 WorkspaceID、事件 ID、会话指纹、
租约和 receipt 的耐久 journal，因此断线、重启、WebSocket 漏事件不会丢任务或重复发送。

若将来需要更强 Windows 原生服务控制，可把 Service/Job Object/Named Pipe 的薄宿主迁到 Rust
`aep-host`，继续使用这套 JSON 协议；Python 的协调内核、测试和业务规则不整体重写。

## 5. 上线验收门（缺一不可）

| 门 | 必须看到的证据 | 不算证据 |
|---|---|---|
| G1 引导 | 用户登录后 Guardian 与它唯一的 Desktop Host 子进程均有 PID+创建时间+父子关系，并在 Windows 报 `child_containment=WINDOWS_JOB` | Task Scheduler 显示 Running |
| G2 子进程恢复 | 隔离任务中只杀 Guardian 创建的 Host，或在实机任务中杀掉已核验的自有 Host 后看到同一 Guardian 的新 PID；End 后 Guardian/Host 旧 PID 都退出、Run 后为新 PID | XML 有 RestartOnFailure |
| G3 退避 | 连续失败达到预算后记录 `RESTART_BACKOFF`，没有新增进程风暴 | 日志中反复“尝试启动” |
| G4 会话边界 | Guardian/Host 只在严格 profile、原会话 marker、session lease 都匹配时尝试输入 | 窗口存在、侧栏名称相似 |
| G5 真实派工 | `SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK` 同事件证据链 | 信箱投递、输入框有草稿、客户端 return code |
| G6 资源治理 | 只回收 AEP 已拥有的 worker/job；核验父子链、创建时间和退出后剩余 PID | 按 `python/node/conhost` 全机通杀 |

## 6. 运行状态口径

- `READY`：Core 与 Desktop 心跳、进程身份和模式均可验证，且 Desktop 是授权 dispatch 模式；
- `OBSERVE_ONLY`：循环可读回 ACK 证据但不会触碰 UI；
- `HOLD`：心跳、身份、Guardian-Child 对应关系或授权之一未获证实；
- `SUPERVISED`：仅表示 Desktop Host 受到 Guardian 的有界进程监管，**不表示消息已送达**。

这样，厂商额度耗尽、模型卡死、窗口重启和某个主控 Agent 下线都会落入可审计的
`UNAVAILABLE / BACKOFF / HANDOFF_REQUIRED` 状态；Core 可把未完成订单连同快照交给下一个
有能力且有额度的成员，而不是靠人工猜测谁还活着。
