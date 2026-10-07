# 自然语言接入：一句话让新 Agent 加入

严谨命令适合自动化，但真实使用时，新 agent 往往只是“打开了同一个文件夹”。因此推荐在共享工作区根目录放一个极短的自然语言入口。

## 初始化工作区

在共享工作区运行一次：

```powershell
aep workspace-init --workspace K:\PythonProjects5\FlagGems-sglang --agents-md
```

它会生成：

- `.aep/workspace.json`：机器可读配置，记录 workspace / coord_root / kernel_root；
- `AEP_JOIN.md`：给新 agent 读的极简自然语言接入说明；
- `AGENTS.md` 追加一小段提示：打开本文件夹的新 agent 先读 `AEP_JOIN.md`。

## 人类只需要说一句

对新 agent 说：

```text
请加入这个工作区协作。
```

如果有具体任务：

```text
请加入这个工作区协作，接入 T125。
```

如果 agent 支持命令行，它读到 `AEP_JOIN.md` 后会运行：

```powershell
aep join --agent <your_agent_name> --task <task_id> --workspace "<workspace>" --markdown
aep inspect --markdown
aep coord-digest --agent <your_agent_name>
```

如果 agent 不支持命令行，它也能按 `AEP_JOIN.md` 的纯文本规则行动：

- 不默认新建会话；
- 先读 digest / status；
- 写动作前 claim；
- 共享资源或外部写动作前 lease；
- 失败后 guided retry；
- 不盲目重放结果未知的外部写动作。

## 为什么这样更方便

新 agent 打开同一文件夹时，很多系统会自动读 `AGENTS.md` 或根目录说明文件。我们把长命令藏到工作区配置里，让人类只需要给自然语言触发句。

核心暗号：

```text
请加入这个工作区协作。
```
