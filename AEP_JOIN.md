# Agent Evolution Protocol - Join This Workspace

If you are a new AI agent opened inside this folder, join the collaboration before doing work.

Human phrase to you may be as short as:

> 请加入这个工作区协作。

What to do:

1. Treat this folder as the shared workspace: `K:\PythonProjects5\agent-evolution-protocol`.
2. Continue in this chat. Do not create a new long-running chat/session unless the user explicitly asks or the protocol says `allow_new_session=true`.
3. If you can run commands, run:

```powershell
aep route --agent <your_agent_name> --workspace 'K:\PythonProjects5\agent-evolution-protocol' --intent '<actual human request>' --markdown
aep join --agent <your_agent_name> --task AEP-ROBUSTNESS-20261028 --workspace 'K:\PythonProjects5\agent-evolution-protocol' --markdown
aep inspect --markdown
aep coord-digest --agent <your_agent_name>
```

The `aep` CLI auto-discovers `.aep/workspace.json` from this folder. If auto-discovery fails, use:

```powershell
aep --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --kernel-root 'K:\PythonProjects5\agent-evolution-protocol\.aep-kernel' route --agent <your_agent_name> --workspace 'K:\PythonProjects5\agent-evolution-protocol' --intent '<human request>' --markdown
aep --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --kernel-root 'K:\PythonProjects5\agent-evolution-protocol\.aep-kernel' join --agent <your_agent_name> --task AEP-ROBUSTNESS-20261028 --workspace 'K:\PythonProjects5\agent-evolution-protocol' --markdown
```

4. If you cannot run commands, read this file and the relevant task state under `K:\PythonProjects5\agent-evolution-protocol\coordination\task_states`.
   Read only the current task and latest digest/checkpoint; do not load the whole message history.
   State which checks you could not execute. Ask a command-capable agent to perform claims or leases.
   Optional protocol references are at `K:\PythonProjects5\agent-evolution-protocol\docs` in a source checkout, or
   https://github.com/love530love/agent-evolution-protocol/blob/main/docs/SCENARIO_ROUTER.zh-CN.md .
   Those documents need not exist inside this project's own `docs` folder.

5. Before taking over delivery work, run `aep --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' coord-delivery-audit --summary-only` and report the `next_required` evidence. This is read-only and does not wake or message another agent.

First reply template:

```text
我是 <agent>。我在本工作区参与协作。
已读取：<actual files>。已执行：<actual commands or none>。未执行：<checks and reason>。
当前任务：<task or none>。
会话策略：优先 sticky session，不默认新建会话。
我是否 claim：<verified yes/no/blocked by owner>。
下一步 bounded action：<one concrete action>。
```

Operating rules:

- If the human phrase includes takeover/review/wake/upload/status, run `aep route --agent <your_agent_name> --intent "<human phrase>" --markdown` first.
- Ordinary messages do not wake models.
- Read digest/status before acting on old wake requests.
- Claim before writes.
- Lease before shared resources or external writes.
- After failure, run guided retry or follow `docs/GUIDED_RETRY_POLICY.zh-CN.md`.
- Never blindly replay uncertain submit/upload/publish/delete actions.
