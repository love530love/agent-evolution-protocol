# 桌面原会话五段验收器

> 相关：[UI Profile](UI_PROFILES.zh-CN.md)、[客户端路线校准](CLIENT_ROUTE_CALIBRATION.md)、[紧急投递语义](URGENT_DELIVERY.md)。

## 目的

`coord-desktop-acceptance` 把既有的受限桌面 relay 包成**一份**可审计验收
manifest。它不新增传输通道，不把窗口聚焦、草稿、信箱文件或 CLI 退出码说成已
唤醒；它只评价同一个原会话中的下列五段：

```text
SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

前四段中的任一段没有证据，后续段就不能被当作完整闭环。只有五段均为
`VERIFIED` 时，`verdict.state` 才是 `COMPLETE`。

## 使用前提

1. 在目标工程根目录执行，使用项目隔离运行时中的 `aep.exe`。
2. `coordination/ui_profiles/<agent>.json` 必须是该客户端当前、经过校准的
   原会话 profile；严格 profile 还必须带有有效的四区 `session_proof` 与
   `route_calibration`。不能把另一个客户端、国内版/国际版或旧缩放比例的资料复制过来。
3. 消息正文必须含本次唯一 `--event-id`，并明确要求目标在**当前会话**回复该
   事件的 `AEP-ACK`。若需要闭合第五段，也要要求一个只会由目标结果产生的
   `AEP-RESULT:<event-id>` 标记。
4. 同一原会话仍受共享 session lease 与全桌面输入锁保护。忙碌、租约冲突、
   会话/模型改变或发送不明，都会停止并保留证据，绝不切换通道或新建会话重发。

## 先预演：默认没有桌面副作用

以下是一条**单行** PowerShell 命令。它只校验 profile 和请求、生成内存中的预演
manifest；不会启动客户端、不会输入、不会点击、不会发送，也不会写出 receipt。

```powershell
& '.\.tools\agent-runtime\Scripts\aep.exe' --coord-root '.\coordination' coord-desktop-acceptance --agent 'qoder_cn' --event-id 'AEP-ACCEPT-EXAMPLE-01' --text-file '.\coordination\orders\AEP-ACCEPT-EXAMPLE-01.txt' --result-marker 'AEP-RESULT:AEP-ACCEPT-EXAMPLE-01' --manifest-file '.\coordination\acceptance_receipts\AEP-ACCEPT-EXAMPLE-01-qoder_cn.json'
```

预演应返回 `mode: "PREVIEW"` 与 `verdict.state: "PREVIEW"`。这不是“已投递”，
也不是“客户端可用”的证明。

## 经过明确授权后执行一次

在已审核预演、确认原会话仍正确且用户明确授权这条消息后，才在同一条命令末尾加
`--apply`。这是一次**单目标、单 relay**执行；它复用既有 `ui_fallback.run` 的
严格定位、会话租约、输入互斥、消息可见与 challenge ACK 逻辑。

```powershell
& '.\.tools\agent-runtime\Scripts\aep.exe' --coord-root '.\coordination' coord-desktop-acceptance --agent 'qoder_cn' --event-id 'AEP-ACCEPT-EXAMPLE-01' --text-file '.\coordination\orders\AEP-ACCEPT-EXAMPLE-01.txt' --result-marker 'AEP-RESULT:AEP-ACCEPT-EXAMPLE-01' --manifest-file '.\coordination\acceptance_receipts\AEP-ACCEPT-EXAMPLE-01-qoder_cn.json' --apply
```

默认不允许为此启动新客户端。只有已有显式启动授权、且 profile 的启动策略也允许时，
才另行加 `--allow-launch`；它仍不等于找到了正确原会话。忙碌时的 `--urgent-interject`
也只可用于已实测、精确声明插话动作的 profile，且拒绝优先于中断模型。

## 回执与状态解释

执行模式会先保留既有 relay receipt，再在
`coordination/acceptance_receipts/`（或 `--manifest-file`）写出一个原子 manifest。它包含
请求与 profile 的哈希、relay 摘要、五段状态、下一个必须完成的阶段和
`manifest_sha256`；消息正文不因这份摘要被重复写入。

| `verdict.state` | 含义 | 正确动作 |
| --- | --- | --- |
| `PREVIEW` | 尚未运行 relay | 审核 profile、消息、事件 ID；它不含任何投递结论。 |
| `HOLD` | 当前所需阶段未被证明 | 修复该阶段的前置条件；不要改通道重发。 |
| `AWAITING_ACK` | 已证实消息可见，尚无同事件 target ACK | 在同一原会话有界观察或由目标稍后回复；不可把可见消息升格为 ACK。 |
| `AWAITING_TASK_RESULT` | ACK 已核验，但没有可归属的结果标记 | 在同一原会话读取实际结果；不要把 ACK 文本冒充成果。 |
| `UNCERTAIN` | 发送、中断、会话或模型状态不明 | 先 `RECONCILE_ORIGINAL_SESSION`；严禁自动重复发送。 |
| `COMPLETE` | 五段均有证据 | 可将 manifest 连同原 relay receipt 交给后续任务验收。 |

`MESSAGE_VISIBLE` 仅代表同一原会话里能读回本事件，不代表模型已开始、已理解或已
完成任务。`AGENT_ACK` 必须来自目标的 challenge 回复；`TASK_RESULT_READBACK` 必须
是同一会话中能看见的、非本方回显的结果标记。

## ACK 后的只读结果回收

若首轮 relay 已收到 ACK、但结果在稍后才出现，**不要重发原消息**。先由常驻/人工的
只读采集把目标原会话的新行发布到 `transcript_bus`，再运行下面的单行命令。它不构造
桌面 driver，不启动客户端，不输入、不点击、不发送；它只读取同 agent、同
`session_marker` 的总线事件，重新生成 manifest 的第五段。

```powershell
& '.\.tools\agent-runtime\Scripts\aep.exe' --coord-root '.\coordination' coord-desktop-acceptance --agent 'qoder_cn' --event-id 'AEP-ACCEPT-EXAMPLE-01' --text-file '.\coordination\orders\AEP-ACCEPT-EXAMPLE-01.txt' --result-marker 'AEP-RESULT:AEP-ACCEPT-EXAMPLE-01' --manifest-file '.\coordination\acceptance_receipts\AEP-ACCEPT-EXAMPLE-01-qoder_cn.json' --readback-only
```

该命令会写入本地 manifest（模式 `READBACK_RECONCILE`），但不触及客户端。它只接受
`assistant/incoming` 且会话标识匹配的事件；来自本方输出、未知会话或其他会话的
结果标记都不会闭合第五段。

## 失败与恢复边界

- profile 无效、会话不唯一、模型标记改变、composer 不唯一或桌面锁忙：不输入，
  记录 `HOLD` 或严格拒绝状态。
- 已输入/已发送但消息可见无法确定，或 relay 被中断：记 `UNCERTAIN`。先只读核对
  原会话历史和已有 receipt；禁止通过邮件、另一个窗口、另一个 agent 名或新会话
  “补发”。
- ACK 超时：保持 `AWAITING_ACK`，不把 timeout 解释为失败后可立即重试。
- 结果标记缺失：保持 `AWAITING_TASK_RESULT`。这说明通信或任务可能仍在进行，
  不是可宣布完成的产物。

这份命令的价值是把一次真实执行的证据边界固定下来。它不能替代目标客户端的登录、
额度、模型响应或厂商 API，也不能把本地单元测试变成真实原会话验收。
