# 多实例 Agent 会话身份策略

## 命名要求

同一 Agent 存在多个版本或地区实例时，窗口标题必须可区分，建议格式：

```text
<Agent>-<Region>-<Workspace>-<Role>
```

示例：

```text
WorkBuddy-CN-FlagGems-Executor
WorkBuddy-INTL-AEP-Reviewer
Qoder-CN-FlagGems-Compiler
Qoder-INTL-AEP-Researcher
```

禁止仅使用 `WorkBuddy`、`Qoder` 等重复标题接收自动任务。

## 安全接入条件

会话只有同时满足以下条件才可被唤醒：

1. 进程、窗口标题和 PID 可唯一定位；
2. 会话声明了当前工作区绝对路径；
3. 工作区存在有效 `AEP_JOIN.md` 或 `.aep/workspace.json`；
4. Agent 已发布 presence/heartbeat；
5. 任务目标、工作区和会话指纹一致。

缺少任何一项时只能读取状态或写入文件信箱，不得自动输入、点击、上传或提交。

## 重启与漂移

Agent 重启后 PID 会变化，不能沿用旧 PID。适配器必须重新发现窗口并以标题、工作区路径、会话指纹和最近 ACK 重新锚定；锚定失败时任务进入 `WAITING_FOR_ANCHOR`，由其他已锚定 Agent 接力。

## 版本和地域

国内版、国际版、测试版和生产版必须使用不同 instance ID。instance ID 写入 presence、唤醒事件、回读证据和能力评分，避免把不同版本的结果混在一起。

## 验收

自动任务只有在目标会话回读出正确 workspace 和 instance ID 后才进入 `MESSAGE_VISIBLE`；窗口可见但身份不匹配时必须拒绝执行。
