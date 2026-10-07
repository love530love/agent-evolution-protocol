# 新 Agent 如何接入协作机制与任意任务

`aep join` 是新 agent 的统一入口。它不会调用模型，也不会创建会话；它只生成接入包，告诉 agent 当前该读什么、能不能接续旧会话、是否能 claim 任务，以及哪些动作被禁止。

## 接入协作机制

```powershell
aep --coord-root coordination --kernel-root .aep-kernel join --agent hermes_desktop --markdown
```

输出会包含：

- 当前 agent 的 sticky session 策略；
- continuation plan；
- unread digest 数量；
- 首读命令；
- 操作纪律；
- 如果无法接续旧会话，会明确显示 `BLOCKED_HANDOFF_REQUIRED`。

## 接入任意任务

```powershell
aep --coord-root coordination --kernel-root .aep-kernel join --agent hermes_desktop --task T125 --workspace K:\PythonProjects5\FlagGems-sglang --markdown
```

如果任务未被 claim，输出会给出：

```powershell
aep --coord-root coordination coord-claim --agent hermes_desktop --task T125
```

如果任务已被其他 agent claim，输出会显示 owner。新 agent 应该读取 owner 状态、digest 和 task-state，不要抢写。

## 给任意新 agent 的最小提示

把下面这段发给新 agent 即可：

```text
你已加入 Agent Evolution Protocol 协作机制。先运行：

aep --coord-root coordination --kernel-root .aep-kernel join --agent <your_agent_name> --task <task_id> --workspace <workspace> --markdown
aep --coord-root coordination --kernel-root .aep-kernel inspect --markdown
aep --coord-root coordination coord-digest --agent <your_agent_name>

不要默认新建会话；不要消费旧 wake；写动作前 claim；共享资源或外部写动作前 lease；失败后先 coord-guided-retry。
```

## 接入后的第一条回复模板

```text
我是 <agent>。我已读取 join packet / inspect / digest。
当前任务：<task or none>。
会话策略：<RESUME_EXISTING / BLOCKED_HANDOFF_REQUIRED / NEW_SESSION_WITH_HANDOFF>。
我是否 claim：<yes/no/blocked by owner>。
下一步 bounded action：<one concrete action>。
我不会新建会话或重放不确定写动作，除非用户或协议明确允许。
```
