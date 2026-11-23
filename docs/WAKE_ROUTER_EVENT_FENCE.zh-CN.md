# Wake Router 事件防重和不确定状态

`wake_router.route(..., execute=True)` 在调用任一传输器前，按事件 ID 取跨进程锁并持久写入 `IN_FLIGHT`；事件 ID 用 SHA-256 映射到协调根 `wake_router_events`，不会直接成为文件名。记录只保存事件 ID、状态和请求摘要，不保存正文。摘要覆盖正文、Agent、已解析工作区、provider、会话及 conversation ID。

同一协调根内，相同事件和请求完成后再次调用返回已记录状态并标记 `already_recorded=true`，不会再次执行适配器；同一事件改变目标或内容返回 `EVENT_ID_CONFLICT`。如果适配器执行期间崩溃、回执写入失败、记录损坏、锁超时或原会话不能解析，返回或保留 `UNCERTAIN`，不能自动换通道重发。适配器抛异常后同样停止。

`IN_FLIGHT` 是保守记录：进程可能在真正输入之前退出，也可能已经发送。**两种情形都不证明消息已送达或未发送。** 必须对照原会话和事件逐项核验，再由上层具备审计能力的接力流程处理；本模块当前没有自动解除或 reconcile API，不能通过删文件、重命名或换事件 ID 来规避不确定状态。连同判定 `FAILED` 或 `INPUT_SUBMITTED` 的已记录事件，重复调用也不会自动重试。

适配器显式提供与事件、会话匹配的 `visible_event_id` / `visible_session_id` 仅能推进 MESSAGE_VISIBLE；需要匹配 `ack_event_id` / `ack_session_id` 的独立回执才可推进 ACK。适配器声称没有输入只是一项自报，不等于独立证据。

## 范围和限制

- 此防重只覆盖本模块路由，其他直接走 commander/UIA/CDP/CLI 的路径不会自动读取这些事件记录；不同协调根也互相独立。当前项目中未发现 wake router 的生产调用方，不能把测试通过宣传成真实客户端已受保护。
- 本机制不替代 `session_lease` 的原会话级锁和不确定态。不同事件、不同渠道可能仍并发触达相同会话；生产接入前必须统一稳定 session binding、共享租约、事件账本和 crash reconciliation。
- 文件内容刷写后原子替换；极端断电下目录元数据、文件系统和硬件的持久性保证尚未实机验证。需要保留并正确备份协调目录，迁移不得静默丢失该账本。
- 并发、进程终止和写入异常测试是受控故障注入，不证明真实桌面消息可见、Agent ACK 或结果完成。
