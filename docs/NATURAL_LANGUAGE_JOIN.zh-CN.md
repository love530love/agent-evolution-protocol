# 自然语言接入：一句话让新 Agent 加入

严谨命令适合自动化，但真实使用时，新 agent 往往只是“打开了同一个文件夹”。因此推荐在共享工作区根目录放一个极短的自然语言入口。

## 初始化工作区

在共享工作区运行一次：

```powershell
aep workspace-init --workspace 'K:\PythonProjects5\MyProject' --agents-md
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

如果 agent 支持命令行，它读到 `AEP_JOIN.md` 后先运行路由，按路由结果读取摘要并选择下一步：

```powershell
aep route --agent hermes --intent "请加入这个工作区协作，接入 T125" --markdown
```

新 agent 的名字可以用安全的字母/数字/下划线/连字符标识，例如 `hermes` 或 `team-reviewer`。这个名字用于文件协作身份，不会自动创建该产品的唤醒适配器。

没有具体任务时只需说“请加入这个工作区协作”，先了解当前状态，不必为完成接入而临时创造任务。还可以说：

- “接入文档改进任务 DOCS-INTRO。”
- “只审查 qoder 的 T125 方案，不执行。”
- “接手 WorkBuddy 卡住的 T125，先确认上次提交结果。”
- “我们各自独立想一个方案，稍后再比较。”
- “汇报目前上传结果。”

通用任务 ID 可通过 `--task DOCS-INTRO` 明确指定。共享项目以外的目录调用时加 `--workspace 'K:\PythonProjects5\MyProject'`；不需要宿主支持多个工作区才能加入。任务 ID 规范、场景词表及决策解释见[自然语言协作路由](SCENARIO_ROUTER.zh-CN.md)。

如果 agent 不支持命令行，它也能按 `AEP_JOIN.md` 的纯文本规则参与：

- 不默认新建会话；
- 先读可访问的 digest / status 文件，仅展开当前任务相关细节；
- 需要认领任务时，由命令型 agent 协助完成并确认结果；
- 使用共享资源或外部写动作前，由有能力的执行方取得 lease；
- 失败后 guided retry；
- 不盲目重放结果未知的外部写动作。

它的首次回执应只陈述已完成的事实，例如：“我已读取 AEP_JOIN.md；我只能读文件，尚未执行 inspect 或取得 claim；我可以先审查现有方案。”不要把接入说明当作操作成功证明。

## 延续长任务

同一项目打开在多个产品里，并不意味着这些产品共享聊天上下文。系统以任务、摘要和检查点连接它们；同一产品内优先复用已绑定会话。缺少绑定或宿主不支持原会话续接时，应清楚给出交接需求。

每次交接先读目标、当前 owner、最新证据位置、未决问题和下一步。详细历史按需查阅，避免把所有聊天灌进当前窗口。普通通知只记入协作状态，显式 wake 才请求执行；静默时无需模型轮询。真实唤醒和旧会话恢复要由接收方适配器确认。

## 更自然的入口：先 route

如果人类说的不只是“加入”，而是“接手、审查、通知、上传、汇报状态”等，优先运行：

```powershell
aep route --agent <your_agent_name> --intent "<人类原话>" --markdown
```

`route` 不会修改任何状态，只会判断场景并给出第一批命令。常见场景包括：

- `project-orientation`：只加入项目、汇报态势；
- `task-join`：接入具体任务；
- `takeover`：接手卡住或失联任务；
- `review-only`：只审查，不执行，可提出低成本证伪；
- `isolated-exploration`：多个 agent 各自探索方案；
- `shared-resource-lock`：浏览器、上传、提交、发布等共享资源；
- `stalled-recovery`：先诊断卡住/失联，再恢复或接管；
- `wake-agent`：唤醒或通知另一个 agent；
- `status-report`：只汇报当前状态；
- `cli-less-fallback`：agent 不能运行命令，只能读文件。

## 为什么这样更方便

支持项目指令的 agent 打开同一文件夹时，可以从 `AGENTS.md` 找到入口。没有自动读取功能的产品，补一句“先读项目根目录 AEP_JOIN.md”即可。详细命令放在工作区配置和路由建议里，人类主要表达任务意图。

核心暗号：

```text
请加入这个工作区协作。
```
