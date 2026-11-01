# dsh-superagent-prototype 调研（2026-11-02）

> 全部事实取自一手来源：GitHub 仓库 API、`README.md`、`docs/000`–`docs/003`、`docs/007`、`SETUP.md`。
> 二手报道（腾讯云社区、博客园、钛媒体、知乎）只用来说明「外面怎么读它」，且逐条标注。
> 调研时点：2026-11-02 01:20 GMT+8。

---

## 一、事实

| 项 | 值 | 来源 |
|---|---|---|
| 仓库 | `deepseek-ai/dsh-superagent-prototype` | GitHub API |
| 描述 | `Towards a More Autonomous Agent System` | API |
| 许可 | MIT | API |
| 语言 | TypeScript | API |
| 建仓 | 2026-10-31 09:32:46 UTC | API |
| 提交数 | **1**（`6337fc82`，2026-10-31 12:05:39 UTC） | API |
| 体积 | 775 KB | API |
| 星 / fork / open issue | **6499 / 637 / 0** | API（2026-11-02 01:20 查） |
| 目录 | `docs/`（000–008 共 9 篇）、`prompts/`、`skills/`、`src/`、`tests/`、`examples/`、`locale/`、`scripts/` | README |
| 宿主 | DeepSeek Harness（DSH）的一个 bundle，不是独立服务 | README + `package.json` |

**建仓到唯一一次推送只隔 2 小时 33 分。** 这意味着仓库里**没有任何演进痕迹**：没有「先做 A 发现不行改成 B」的历史，没有 PR 讨论，没有 issue 里作者的解释。想看取舍过程，只能读那 9 篇文档。

README 自己的定性，必须原文引：

> **Research prototype for reference only, not an official DeepSeek product.** … Treat it as research code, not production-ready software. It may still contain bugs, and its coding style and engineering practices may be inconsistent.
>
> We have adapted it for publication, so it is **not identical to our internal version** or the implementation described in *Exploring Super-agent Design*. Some designs discussed in the article are **directions for future development and are not implemented in this prototype**.

所以「DeepSeek 的超级 Agent 就是这么实现的」这个说法不成立——作者自己先否了。

### 定位

> a proactive coding agent for **one GitHub repository**, packaged as a bundle for DeepSeek Harness. It listens to GitHub and, optionally, Feishu. When someone allowed asks the agent for work, it opens a work item, works on it in its own agent session and directory, and answers in the original conversation, for example with a pull request.

一句话：**常驻的 Coding Agent，钉死一个仓库，接 GitHub（可选飞书），被允许的人 @ 它就开工单，在独立会话和目录里干，回到原对话交付（通常是 PR）。**

---

## 二、它真正在解决的不是「自治」，是「授权」

最容易被读错的是标题里的 autonomous。`docs/000-philosophy.md` 第一句就把判据换掉了：

> **Take as little human attention as possible, and give as much value as possible.**
>
> Every part of the project is judged by the attention it costs. A message, a notification, a question, a setting, a panel element and a paragraph of documentation each cost someone's attention, and each has to give more value than that, or it is removed.

**判据是「注意力成本」，不是「能力上限」。** 所以文档里花最多篇幅的不是「Agent 能做什么」，而是「什么时候不许做、什么时候不许说话、做完凭什么继续」。

由此推出一条我认为整份项目最锋利的设计原则：

> **Editing a part changes the instructions the model receives. It does not change the gate, Mandates, tool checks or account wrappers, which remain enforced by code.**

（`docs/004`，二手报道引用，一手 README 与 002 的表述一致：`The host enforces allowed lists, rules and Mandates. Messages, comments and quoted documents are inputs to a decision; they cannot change those permissions.`）

**提示词不是权限。** 权限在代码里硬校验，提示词只负责判断质量。这条把「写一句『不要动生产环境』然后指望模型听话」这种最常见的偷懒直接排除了。

---

## 三、11 个概念里最值得看的一个：Mandate

`docs/001-domain.md` 定义了 11 个概念：Source、Trigger、Thread、Correlation、Channel、Policy、Eval、Mandate、Ticket、Assignment、Subscription。其中 **Mandate** 是别处没见过的东西：

> **Mandate** — What an accept grants: the Policy version, the scope, the strength, the matched rules, the sender's identity on that Source and how the Source received the Trigger.
>
> Rules: **Immutable**; wider scope means a new Ticket. A Ticket's end does not end it: **only a person's stop withdraws it**, and a Policy that no longer holds it revokes it. **Revalidated against the current Policy before an ended Ticket resumes.**

翻成一句话：**一次「可以做」的决定被固化成一张凭证，写着「凭什么、能到哪、谁批的、按哪一版规则批的」。**

四条硬规则值得逐条看：

1. **不可变**。范围要扩大，不是改这张凭证，是开新工单。
2. **工单结束不等于凭证失效**。只有人明确叫停才收回，或「当前 Policy 不再支持它」才吊销。
3. **复活前要重新校验**。已结束的工单有人说「接着弄」，先拿旧凭证对当前 Policy，对不上就作废。
4. **Policy 变更不立刻吊销在途凭证**——只记录并投递一条 `policy-changed` Input 给受影响的工单。文档在「被否决的概念」表里给了理由：

> Cancelling running Tickets on a Policy change → **A cancelled change leaves half-finished work**; the change is recorded and delivered instead.

**它宁可在规则收紧后允许在途工作跑完，也不制造一次中断。** 这是产品判断，不是技术限制。

那张「被否决的概念」表共 11 行，每行都是「我考虑过 X，因为 Y 不要了」。其中两条和 AEP 直接相关：

> **Inferring "waiting on a person" from a report's free text** → The classification needs **structured facts**; the Executor asks through a structured field.
>
> **Locking all of an Executor Session's tools when it fails or blocks** → It would **refuse a person working directly in the Session**, and the report after recovery; pausing automatic input is enough.

---

## 四、三个反常识的默认值

### 1. 准入表：陌生人 @ 它，默认不理

每个 Source 有一张三格准入表（`docs/002`）：

| 情形 | 可选 | **默认** |
|---|---|---|
| 在白名单里，但没 @ | inferred / ignore | **inferred** |
| 不在白名单，但 @ 了 | explicit / inferred / ignore | **ignore** |
| 不在白名单，也没 @ | inferred / ignore | **ignore** |

第二行是最反常识的：**大多数 Agent 产品里「被 @」就等于「被召唤」，这里不是。** 只有「名单内 + 明确点名」才算 explicit request。

### 2. 接受模式默认 `observe`：只写日记，不干活

| 模式 | 效果 |
|---|---|
| `off` | 推断出来的候选一个都不算 |
| **`observe`（默认）** | 推断出来的候选照常评估，结论「我会接受」，但**不真的接受**，只记一条标着 "would accept" 的 Decision |
| `auto` | 评估通过就真开工 |

**默认配置下这个 Agent 一件主动的活都不会干，它只写日记。** 文档把上生产路径写成：先在 `observe` 跑一段，翻完记录下来的 Decision，确认「它想干的正是我想让它干的」，再切 `auto`。

这和大多数团队的实际顺序（先开全自动，出事再关）正好相反。

### 3. 自动合并默认关

`SETUP.md` 第 9 问的推荐答案是「否」：

> [No: **people merge until the agent's pull requests have been right for a while**.]

并且规定 Agent **不能**用自己的账号开自动合并，必须用另一个账号的 GitHub CLI。

---

## 五、与 AEP 的对照（本报告的重点）

我逐条比了 DSH 的设计文档与 AEP 刚完成的 41 条审计发现。结论是：**AEP 审计出的问题里，至少 6 条在 DSH 的文档里已经有设计答案；反过来 DSH 缺的 4 件事 AEP 有。**

### 5.1 AEP 的缺陷 ↔ DSH 已有的答案

| AEP 发现 | DSH 的设计答案 | 差距性质 |
|---|---|---|
| **A-03** handoff 后闸门问错了订单，回执写 `not_sent`，原始订单永远没人确认 | **released follow-ups**：工单结束时，释放执行器未读/未接的 follow-up，每条在无其他工单持有时**进入 Eval 重新判定** | AEP 把未确认的事实留在前任那张单上，**没人接管**；DSH 有明确的接管规则 |
| **A-01/R-01** `role`/`direction` 从未被赋值，真实 ACK 永远判 `not_assistant` | DSH 把它写成 **open question**：「哪些 Trigger 字段可信（actor、Thread 归属），哪些不可信（body、quotes）」 | AEP 的实测给出了这个问题的另一半答案：**你以为可信的字段，可能根本没被赋值** |
| **R-06** `complete`/`add_task` 丢写，日志说做完、状态说没开始 | **One writer per state**；`HealthBoard` 拒绝第二个认领者；Source 只能通过窄接口 `SourceContext` 改状态 | 原则 AEP 也有（`engine.update()` 的 docstring 明写了这个坑），但**一半写入口绕过它**。DSH 是结构性强制，AEP 是注释 |
| **S-06** 单 agent 不回话 → `blocked`，`revive` exit 2 说「clear that reason」，而 CLI 里没有 clear | 明确**否决**「失败/阻塞时锁住执行器全部工具」，理由「会拒绝人直接在 Session 里继续工作，以及恢复后的报告」 | DSH 的答案是「不要锁」；AEP 的答案是「锁住且不给钥匙」 |
| **S-13** inbox 无界增长，单拍 O(任务×文件) | DSH 直认：**「Nothing deletes old records yet, so these grow without bound」** | 两个项目独立踩了同一个坑。DSH 至少写在文档里，AEP 是 SRE 测出来的 |
| **A-04/S-15** `state.json` 无锚，手改不可检测 | role-based `Pick`（`EngineForSources` / `EngineForMainSession` / `EngineForPeople` / `EngineForSettings` / `EngineForExecutor`）——**用类型把能力切窄** | DSH 靠类型系统，AEP 靠运行时校验。方向不同，但 DSH 的做法更难绕过 |

### 5.2 四处独立收敛（说明 AEP 的方向没错）

1. **`observe` 默认 ↔ `ack_policy="record"` 默认。** 两边都把「记录但不行动」当默认值，理由也一致：先看清它会做什么，再放权。
2. **Policy 变更不吊销在途 Mandate ↔ AEP 的策略进化红线。** AEP 规定已晋升策略只能在 `{retry, handoff, switch_agent, cool_down}` 内选，不能把 `escalate/pause` 降级；DSH 规定规则收紧不打断在途工单。**两边都在防「规则变更追溯性地毁掉在途工作」。**
3. **「人不是领域概念」↔ AEP 的 `workbuddy` / `workbuddyai` 撞名事故。** DSH 的原文：

   > A person is not a domain concept. A Trigger carries its actor's identifier as the Source gives it. … Identities on different Sources are **never mapped to each other**.
   >
   > Rejected: Principal or Membership as an entity, or mapping identities across Sources → … a cross-Source mapping is **prompt text**.

   **AEP 恰恰栽在一张全局名字表上**——两个 agent 用了同一个登记名，还互相覆盖了对方的 claim。DSH 的立场是：身份是 Source 自己的元数据，跨源一致性交给提示词，系统不维护那张永远会漂移的表。这个取舍很值得 AEP 抄。
4. **「不从自由文本推断等待状态」↔ AEP 的 `role` 推断。** DSH 明确否决「从报告的自由文本里推断在等人」，理由是「分类需要结构化事实」。**AEP 违反了它自己这条原则**：ACK 判定依赖一个从未被赋值的结构化字段，看起来是结构化事实，实际是空的。

### 5.3 AEP 有、DSH 没有

| AEP 的能力 | DSH 的状况 |
|---|---|
| **hash chain 事件日志**（删改可检测） | 无链。且记录**无界增长**，没有删除机制 |
| **quorum N-of-M 独立签名**（不可逆动作） | 只有「一个人说 stop」 |
| **崩溃一致性实测**（`atomic_write` 被 `taskkill /F` 验过） | 文档无这一层。只有「重启时每个 worker 的背景工作最多等 10 秒」 |
| **对自身缺陷的量化审计**（41 条、20 个复现脚本） | 有 2 条 open questions，但**没有任何测量数字** |

### 5.4 DSH 有、AEP 没有

| DSH 的设计 | AEP 的缺口 |
|---|---|
| **授权与上下文彻底分离**：`Subscription` 投递上下文但「**bypasses the gate and grants no new authority**」 | AEP **没有独立的授权对象**。授权是从「发给这个 agent 的最新一张订单」**推断**出来的——这正是 A-03 的根因 |
| **Mandate 不可变 + 恢复前按当前 Policy 重校验** | AEP 的 `revive` 不对当前策略重校验 |
| **准入表 + 保守默认** | AEP 无等价物：**能写订单的人就能驱动** |
| **按角色收缩执行器能力**（worker 拿不到向人提问/改配置/开其他会话的工具） | AEP 的 adapter 是对称的，派工单里就带着 `coord-claim` 命令 |
| **结构化 `Attention` 对象**（一个待回答的问题是一个实体） | AEP 用自由文本 + `held[]` 行 |
| **面板只显示需要人的东西**（"A healthy state takes no space"） | AEP 有 `status`，但没有「现在需要我做什么」视图（这正是 F-14 要补的） |

### 5.5 一条我认为最该立刻抄的

**把 AEP 的 `claim` 拆成「授权」与「上下文」两件事。**

DSH 的原文：

> Every message on a subscribed Thread reaches its subscribed Tickets as context, including messages from bots and CI. **This delivery bypasses the gate and grants no new authority. Each Ticket remains limited by its Mandate.**

AEP 现在的问题是：**「这张单有没有被确认」这件事，是从「路由状态」（最新订单）推断的，而不是从「授权对象」读的。** handoff 一发生，路由变了，授权事实就丢了——A-03 不是实现 bug，是**没有那个对象**。

DSH 的 `Mandate` 是一个独立、不可变、可重校验的对象，所以「谁批的、批到哪、按哪版规则」在任何时刻都能直接读出来，不需要推断。

**这直接改变了 F-07b 的修法**：我原来写的是「handoff 时把未 ACK 事实写进任务的 `inherited_unacked` 字段」——那还是在路由对象上挂补丁。按 DSH 的思路应该是**给 AEP 补一个 `mandate` 对象**（或复用 `claim` 记录，把它从「路径租约」升级成「授权凭证」），`ack_verdict` 从 mandate 读，不从「最新订单」读。

---

## 六、该怀疑的地方（全部有一手依据）

1. **一个 commit = 没有演进史。** `6337fc82` 是唯一一次推送，作者是团队名 `DeepSeek Harness Team`，无个人署名、无 PR、无 issue 讨论。**你能看到结论，看不到取舍的过程。** README 自认 "adapted for publication"，与内部版本和那篇文章描述的实现都不完全一致。
2. **6499 星 / 637 fork / 0 open issue 不代表稳定。** fork/star ≈ 9.8%，接近「有人真在部署」的水平，但 **0 issue 更可能意味着「还没人真跑起来」或「维护者没开讨论区」**，不是质量指标。建仓至今不到 2 天。
3. **最硬的问题它自己说没解决。** `docs/007` 末尾 open questions 只有两条，第二条之外的第一条就是信任边界：

   > Trust: **which Trigger fields are trusted**, such as the Source's actor and Thread membership, and which are not, such as the body and quotes.
   >
   > Deployment isolation: the agent's profile on its own pinned DeepSeek Harness installation, so upgrading DeepSeek Harness elsewhere does not break it.

   **信任边界是 Agent 系统最硬的问题，而这个原型明确把它列为未解决。**
4. **执行器隔离靠输出过滤，不是存储隔离。** `docs/007`：执行器「reads only its own Ticket… and its pages **leave out the ids** of what it cannot read」——这是**不给你看**，不是**不让你读**。
5. **记录无界增长。** `docs/007` 直认：「Nothing deletes old records yet, so these grow without bound」，包括 trigger、disposition、decision、已结束工单、outbox 收据、所有设置版本与 prompt 版本。
6. **插件化不彻底。** `docs/007` 有一节 "Source-specific dependencies"，列出通用模块直接引用了 GitHub 或飞书（Outbox 硬编码飞书字数限制、`links.ts` 解析飞书消息链接、Setup 只探测 GitHub/飞书账号字段等）。**「Source 插件化」的抽象并不彻底。**
7. **规模上限未披露。** `docs/007` 给了队列并发（assessment 4、delivery 4、relay/notice/reply 各 1）、pass 每 2 秒一次、worker 槽位、以及一堆字符/条数上限（Trigger 4000 字符、评估窗口首 5 条 + 两侧各 5 条、`related_tickets` 最多 8 个等），但**没有任何吞吐、成本或延迟数字**。
8. **误判的方向是「悄悄不做」。** 提示词反复要求「不确定就安静忽略」。安全侧方向对，但**漏判不可见**：本该做的请求被 ignore，只留一行带理由的 Decision，没人会去看。对想验证「漏了多少」的团队，需要额外抽样流程，文档没给。

---

## 七、结论：抄什么，不抄什么

### 该抄（按对 AEP 的价值排序）

1. **授权对象化**——`Mandate` 式的独立、不可变、可重校验凭证。**这直接改掉 F-07b 的修法**（从「在路由对象上挂补丁」改成「补一个授权对象」）。
2. **上下文与授权分离**——订阅投递上下文但不授予权限。
3. **准入表 + 保守默认**——尤其「被 @ 不等于被召唤」这一条。
4. **按角色收缩执行器能力**——worker 拿不到「向人提问」的工具，就不会制造需要人回答的噪音。
5. **released follow-ups**——工单结束时显式释放未处理的消息，而不是让它们烂在旧单上。
6. **「人不是领域概念」**——身份是 Source 自己的元数据，绝不做跨源映射。

### 不该抄

- **单仓库定位**：对 AEP 太窄，而且 DSH 自己说这不是技术限制，是「范围说不清就没人敢批」的授权模型后果。
- **提示词作为产品资产**（`Communication` / `Ticket scope` / `Delivery requirements` 三段可改）：AEP 是协议不是产品，没有「团队提示词」这个层。
- **无删除机制**：AEP 已经有 S-13 的教训，不要引入第二个无界增长源。

### 一句判断

**如果你的团队已经在抱怨「Agent 在群里乱说话」，这套东西的价值不在自治，而在它把「闭嘴」做成了默认值。**

对 AEP 而言，更准确的说法是：**DSH 把「凭什么可以做」做成了一个对象，而 AEP 把同一个问题做成了一个推断。** AEP 的 41 条审计里有 6 条，根子都在这个差别上。

---

## 附：一手来源清单

- `README.md` — 定位、免责声明、文档索引
- `SETUP.md` — 前置依赖（Node 22.19+/24+、pnpm、git 2.32+、tmux、Python 3.8+、GitHub CLI 2.48+、DSH 在 `engines.dsh` 区间内）、9 个问题、默认值清单
- `docs/000-philosophy.md` — 注意力成本、对 Agent 的 5 条要求、对设计的 4 条要求、操作者责任
- `docs/001-domain.md` — 11 个概念 + 12 条被否决概念 + 术语表（含中文对照）
- `docs/002-authorization.md` — 两条 Trigger 路径、强度表、准入表、接受模式、Policy 版本、恢复前重校验
- `docs/003-execution.md` — 工单阶段表（7 行）、worker 槽位、重启规则、回合后背景工作 30 分钟上限
- `docs/007-architecture.md` — 双会话结构、工具清单（主会话 14 个 / 执行器 6 个）、单一写入者、open questions、队列并发与容量上限
