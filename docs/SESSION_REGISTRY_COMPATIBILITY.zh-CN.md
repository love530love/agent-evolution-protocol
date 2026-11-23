# 原会话注册：兼容防覆盖

旧注册记录的文件名只包含 `agent` 与 `session_id`，不包含 workspace 或 provider，且斜杠会被替换成下划线。为了兼容历史文件，本次不迁移或重命名文件：

- 重新注册时先检查旧记录的 agent、规范化路径、provider、session ID 是否完整匹配；不同工作区/提供商或文件名碰撞返回 `SESSION_IDENTITY_CONFLICT`，原记录不变。
- 目标记录损坏、不可读取或缺少关键身份字段返回 `SESSION_RECORD_UNREADABLE`，不覆盖。其他损坏 JSON 不会令查询崩溃，但查询并不代表修复这些记录。
- 新版注册和状态变更共用协调目录下的跨进程锁；旧版本若不遵守该锁，仍可能发生并发覆盖，因此部署时不能混用并发写入器。
- `transition` 根据记录原始 agent 和 session ID 写回，避免查询用不同大小写时在区分大小写的系统上产生重复记录。

`resolve` 默认拒绝超过 900 秒的注册记录，调用方可用 `max_age_seconds` 设置有限正数；缺失、无效、无时区或未来时间均拒绝。每次显式注册记录 `registration_refreshed_at`，状态 `transition` 不会刷新它。旧记录缺少该字段时返回 `SESSION_REGISTRATION_TIME_UNKNOWN`，需要重新核验并显式注册；不会把旧的 `updated_at` 冒充注册时间。时间来自本机时钟和本地文件，并非防篡改证明。

**限制**：同一 agent 和 session ID 仍不能在不同 workspace/provider 同时注册；目前会拒绝而非提供多空间支持。后续需要版本化复合键、冲突审计与显式旧记录迁移。即便注册时间新鲜，也不证明进程在线、当前工作区/原会话匹配、会话处于前台或消息已送达；发送前仍需获取真实原会话的独立新鲜证据。
