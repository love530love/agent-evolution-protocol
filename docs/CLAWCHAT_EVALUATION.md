# ClawChat / Cowchat 评估

## 结论

“ClawChat”存在多个同名项目，不能直接把名称当作唯一依赖。最相关的 `cbd/clawchat` 已更名为 Cowchat；旧仓库仅保留 v0.3.x 兼容代码。其协议是本地 TCP/Unix socket + NDJSON，提供房间、投票、决策记录和领导者选举，任何能写 JSON Lines 的 Agent 都可接入。

另一个 `alexrudloff/clawchat` 是跨机器的 P2P 加密聊天，适合跨网络 Agent 网格，不适合作为本机核心总线。

## 与 AEP 的互补

| 能力 | ClawChat/Cowchat | AEP |
|---|---|---|
| 实时群聊 | 强 | 文件/事件总线为主 |
| 房间与投票 | 内置 | 治理与审计可扩展 |
| 唤醒与桌面控制 | 非核心 | UIA/CDP/桥接 |
| 事件证据、ACK、租约 | 需适配 | 核心能力 |
| 本地记忆与回溯 | 项目相关实现 | SQLite/FTS5/JSON/Markdown |

## 建议接入方式

不把 ClawChat 作为硬依赖。新增可选 `clawchat` transport adapter：

1. AEP 生成带事件 ID 的 envelope；
2. 通过 NDJSON 发到已授权的 Cowchat room；
3. 收到消息后写入 AEP SQLite/Markdown 记忆；
4. 投票/决策只作为协商证据，不能替代 AEP 的 `AGENT_ACK`、租约和外部动作验收；
5. Cowchat 不可用时自动回退现有本地总线，不阻塞核心协作。

## 风险

- 旧 ClawChat 仓库已弃用，应以 Cowchat 当前协议为准。
- P2P/跨网络版本引入密钥、节点信任和网络暴露面，必须显式启用。
- 群聊可见不等于任务已接单；仍需事件 ID、ACK 和产物证据。

参考：

- <https://github.com/cbd/clawchat>
- <https://github.com/cowboyinc/cowchat>
- <https://github.com/alexrudloff/clawchat>
