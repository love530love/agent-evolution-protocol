# 协作身份与精确投递（v0.6.47）

## 问题

GitHub Copilot 在回执时只发现了 `workbuddy`，不知道 `workbuddyai` 是本协作区的登记身份，于是把给 `workbuddyai` 的回执投到了另一个 Agent 的信箱。

根因不是“没有信箱”：`coordination/messages/` 下三个目录都存在，`coord-status` 的 sessions 也能列出四个名字。真正的缺口有两处：

1. `src/agent_evolution_protocol/coordination/bridge.py` 的 `AGENTS` 硬编码名单不含 `workbuddyai`；
2. `src/agent_evolution_protocol/cli.py` 的 `AGENTS` 同样硬编码且不含它，`coordination/clients/` 也没有该身份的记录。

队友做发现时看到的是这份名单，不是目录，所以得出“只有 workbuddy”的错误结论。

## 四个互不相同的身份

| 身份 | 含义 | 桌面窗口 |
| --- | --- | --- |
| `workbuddyai` | 本协作区的登记名；协作身份 | 无。`identity_only=true`，不允许通过标题匹配变成投递目标 |
| `workbuddy` | 早期登记的国内 WorkBuddy 身份 | 由 `workbuddy3` 的注册说明标注为旧名，不再作为本 Agent 的收件名 |
| `workbuddy3` | 国内 WorkBuddy 桌面，Windows 实例 | `WorkBuddy.exe`；只走 OCR 投递 |
| `workbuddy_ai` | WorkBuddyAI 桌面客户端 | `WorkBuddyAI.exe`，session `FlagGems-sglang` |

**没有别名映射。** `agent_identity.ALIASES` 故意为空；`canonical()` 只做去空白和转小写，绝不做前缀/包含匹配。写错收件人必须失败，不能被静默重定向到名字相近的邮箱。

## 修复

- 新增单一真理源 `coordination/agent_identity.py`：`SELF`、`WORKBUDDY_IDENTITIES`、`ALIASES={}`、`LEGACY_BRIDGE_AGENTS`、`canonical()` / `is_same()` / `describe()`。
- `bridge.AGENTS` 与 `cli.AGENTS` 都改为引用该常量，不再各写一份名单。
- `client_registry.BUILTIN` 新增 `workbuddyai`，标为 `identity_only=True`：`matches()` 对它恒为 `False`，`discover()` 返回 `windows=[]`。即使有人写错 `clients/workbuddyai.json` 想把标题或 exe 加回来，`profile()` 也会强制恢复该标记，防止误投到别人的会话。
- 运行时 `coordination/clients/workbuddyai.json` 做身份登记（该文件被 gitignore，不随发版；权威定义在源码的 `BUILTIN`）。
- `AGENTS.md` 加入精确投递规则，打开工作区的队友第一眼就能看到。

## 复算命令

```bash
PY=.tools/agent-runtime/Scripts/python.exe

# 1. 两个名单同源，四个身份都在
$PY -c "from agent_evolution_protocol import cli; from agent_evolution_protocol.coordination import bridge, agent_identity as ai; print(cli.AGENTS == bridge.AGENTS == ai.LEGACY_BRIDGE_AGENTS, [n for n in ai.WORKBUDDY_IDENTITIES if n not in cli.AGENTS])"

# 2. coord-status 可见 workbuddyai
$PY -c "from agent_evolution_protocol.cli import main; main()" --coord-root coordination coord-status

# 3. 该身份不对应任何桌面窗口（零输入）
$PY -c "from agent_evolution_protocol.cli import main; main()" --coord-root coordination coord-client --agent workbuddyai discover

# 4. 守线
$PY -m pytest -q tests/test_agent_identity.py tests/test_out_of_scope.py tests/test_desktop_discovery.py
```

预期：`True []`；sessions 含 `workbuddyai`；discover 的 `identity_only=true`、`windows=[]`；测试全绿。

## 边界

- `coord-send --to workbuddyai` 只保证**信箱落盘**，不保证桌面送达，更不是 ACK。只有目标在原会话带事件 ID 的回复才可能构成 `AGENT_ACK`。
- 本修复不授权任何跨身份的指挥权转移；它只保证名字不再串台。
- `identity_only` 不是 `out_of_scope` 的同义词：前者说“这个名字不是窗口”，后者说“这个窗口不允许输入”。两者各自的守线不互相放宽。
