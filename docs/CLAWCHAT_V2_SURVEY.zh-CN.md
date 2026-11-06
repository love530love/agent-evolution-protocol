# ClawChat Protocol v2 官方组织调研（github.com/clawling）

- **采集时间**：2026-11-06 15:50~16:25 GMT+8
- **数据源**：本机已认证的 `gh` CLI 直连 GitHub REST API（只读，未 clone、未安装、未连接任何 ClawChat 服务端）
- **范围**：`https://github.com/clawling` 下**全部**可见仓库（`type=all` 分页确认无遗漏）
- **性质**：事实基线，**不是集成方案**。本项目没有接入官方 v2 通道。

## 0. 结论先行

1. `clawling` 是**组织**（`CLAWLING PTE.LTD.`），不是个人账号；`public_repos=2`，`type=all` 也只有 2 个，无 fork / 可见 private / archived。
2. 两个仓库都是 **ClawChat 官方 Protocol v2 的宿主插件与安装器**，不是可独立运行的服务端。协议是 **WebSocket**，服务端持有 wire contract，插件只是 peer client。
3. **与本项目存在协议代差**：项目 `clawchat_transport.py` 是本地 TCP + NDJSON，`docs/CLAWLING_INTEGRATION.md` 是本机 GUI 的 **v1 / HTTP `/v1/send`**；两者都**不等于**官方 v2 WebSocket，直接当客户端接会不兼容。
4. **不引入运行时**：需要第三方服务端常驻连接、Hermes/OpenClaw 宿主、npm/npx 与 `websockets` 依赖，违反项目「库级无服务、无 DB、无后台线程、不新增 schema 或命令面、默认 dry-run」硬约束。
5. **只引协议与工程规范**：能力卡 + manifest 生成/守线、原子写、具名拒绝、profile 隔离、凭据不泄漏测试，与本项目同构，值得借鉴。
6. 本次调研抓到一个**现成的错误情报样本**：官方 README 说 53 个工具，`docs/reference/tools.md` 说 55，`plugin.yaml` 实际声明 **55**。文档之间会漂移，判定必须以声明文件为准。

## 1. 调研方法（可复算）

```bash
gh api orgs/clawling --jq '{login,type,name,public_repos,followers,created_at}'
gh api --paginate "users/clawling/repos?per_page=100&type=all" \
  --jq '.[] | [.name,.language,.stargazers_count,.forks_count,.fork,.archived,.created_at,.pushed_at] | @tsv'
gh api "repos/clawling/<repo>/git/trees/main?recursive=1" --jq '.tree[] | select(.type=="blob") | .path'
gh api "repos/clawling/<repo>/readme" --jq '.content' | base64 -d
gh api "repos/clawling/clawchat-plugin-hermes-agent/contents/plugin.yaml" --jq '.content' | base64 -d
gh api "repos/clawling/<repo>/releases?per_page=3" --jq '.[].tag_name'
```

> 本机坑：Git Bash 的 `/tmp` 与 Windows Python 的 `/tmp` 不是同一路径，跨两者传文件会 `FileNotFoundError`；用 `C:/Users/.../Temp/` 或管道。

## 2. 账号事实

| 字段 | 值 |
|---|---|
| login / type | `clawling` / **Organization** |
| name | `CLAWLING PTE.LTD.` |
| created_at / updated_at | 2026-04-08T07:18:36Z / 2026-08-04T05:56:54Z |
| followers | 45 |
| public_repos | **2** |
| description | 空 |
| html_url | https://github.com/clawling |

## 3. 仓库全量清单（2 / 2）

| # | 仓库 | 主语言 | Stars / Forks | Issues | Size(KB) | License | 分支 | created_at | pushed_at | topics |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `clawchat-plugin-hermes-agent` | Python | 29 / 3 | 5 | 3967 | MIT | `main` | 2026-05-28T10:42:46Z | **2026-11-04T11:40:55Z** | 空 |
| 2 | `clawchat-plugin-install-cli` | TypeScript | 2 / 0 | 3 | 1133 | MIT | `main` | 2026-05-28T10:44:46Z | **2026-11-03T04:15:56Z** | 空 |

两个仓库 `description` 均为 `null`、`topics` 均为空、`fork=false`、`archived=false`。

### 3.1 `clawchat-plugin-hermes-agent`（Python）

**定位**：Hermes Agent 的 ClawChat gateway 插件。README 明确要求 Hermes **v0.12.0+**，排除 `v0.20.3–v0.20.6`（这四个版本在安装期安全扫描里拦截该插件），建议升级到 **v0.21.6+**。安装路径 `$HERMES_HOME/plugins/clawchat/`（`HERMES_HOME` 默认 `~/.hermes`）。

**权威声明**（`plugin.yaml`，`manifest_version: 1`）：

| 字段 | 值 |
|---|---|
| name / kind | `clawchat` / `platform` |
| version | `0.14.0-125`（与 releases tag 一致） |
| author | `CLAWLING PTE. LTD.` |
| provides_hooks | **7 个**：`pre_api_request`、`post_api_request`、`on_session_reset`、`pre_gateway_dispatch`、`pre_tool_call`、`post_tool_call`、`pre_llm_call` |
| provides_tools | **55 个**（以 `clawchat_*` 为主，含 `clawchat_orchestrate_*`、`clawchat_tool_grant/revoke`） |

**⚠ 文档漂移实例**：README 写「fifty-three `clawchat_*` tools」，`docs/reference/tools.md` 写「There are **55** tools」，`plugin.yaml` 实际声明 **55**。`tools.md` 自己也写明「`plugin.yaml` is the authoritative tool list」。⇒ 以声明文件为准，不采信散文数字。

**注册面**（`docs/architecture.md`）：单一公开入口 `register(ctx)`（仓库根 `__init__.py`），依次调用
`ctx.register_platform("clawchat", …)`（要求 Hermes ≥0.12）、`register_tool`、`register_skill`、
`register_cli_command("clawchat")`、`register_command("/clawchat-activate" | "/clawchat-reclaim" | "/clawchat-output")`、
`register_hook(...)`。命名映射区分 `clawchat`（插件 id）／`clawchat-gateway`（pyproject 元数据，不构建 wheel）／GitHub source spec／`$HERMES_HOME/plugins/clawchat/`。

**协议**：README 与 `docs/client-integration.md` 明确是 **ClawChat Protocol v2（WebSocket）**，「The server owns the wire contract and its implementation takes precedence」；本插件与 `openclaw-clawchat` 是 peer v2 clients。`client-integration.md` 的 Provenance 写「Last full upstream sync: 2026-09-16, server commit `da47fed`」，并列出尚未并入的上游变更（`ack_epoch`、`truncation_check`、JWT `nbf`、session revocation 等）。

**依赖**（`pyproject.toml`）：`requires-python >=3.11`，**唯一运行时依赖 `websockets>=13,<16`** —— 这从依赖侧坐实了它是常驻 WebSocket 客户端。测试依赖 pytest / pytest-asyncio / PyYAML / ruamel.yaml。`[tool.uv] package = false`，刻意不建 wheel（避免多 profile 的 uv workspace 名字冲突）。

**能力卡**：`skills/manifest.json` + 6 个 `skills/*/SKILL.md`：`clawchat-core`、`clawchat-liveware`、`clawchat-liveware-dev`、`clawchat-liveware-sample`、`clawchat-orchestration`、`clawchat-set-greeting`。**与本项目 `skills/README.md` 的「能力卡 + 生成 manifest + 守线测试」结构同构。**

**工程模块**（`clawchat_gateway/`）：`atomic_file.py`、`connection.py`、`group_message_coalescer.py`、`group_command_receipt.py`、`gate_outcome.py`、`clawchat_memory.py`、`compression_status.py`、`device_id.py`、`token_refresh` 相关等；`fixtures/permission_events/` 有具名拒绝样本。20+ 篇 docs 覆盖 `activation`、`instance-locks-and-install-conflicts`、`multiplex-profile-isolation`、`tool-grants`、`reference/prompt-injection`、`reference/http-endpoints`、`history-budget`、`media-boundaries`。

**发布**：releases tag `0.14.0-125` / `124` / `123`；最新 commit `d18f8b1c`（2026-11-04T03:53:52Z，Merge PR #79 pre-release）。

### 3.2 `clawchat-plugin-install-cli`（TypeScript）

**定位**：npm 包 `@clawling/clawchat-plugin-install-cli` 的源码仓库。根 `package.json` 名 `@clawling/clawchat-plugin-install` v0.1.1（`private: true`），`pnpm@9` monorepo（`workspaces: packages/*`），`node >=18`。

**命令**：`install` / `update`，`--target openclaw|hermes`，可加 `--force`；Hermes 另有 `--profile <name>`。实际插件工作委托给宿主的 plugin manager：

- OpenClaw：`openclaw plugins install @clawling/clawchat-plugin-openclaw --force --dangerously-force-unsafe-install`（新版还需 `--accept-capabilities`），并可能迁移旧插件 id 的用户配置；QClaw 会追加 `plugins.load.paths`。
- Hermes：先拉远端 `plugin.yaml` 校验 `hermes --version`，再 `hermes plugins install clawling/clawchat-plugin-hermes-agent --enable`。

**结构**：`packages/cli`（`src/cli.ts`、`index.ts`、`io.ts`）+ `packages/core`（`src/auth/`、`baseurl/`、`config.ts`、`fs/atomic-write.ts`、`http/client.ts`、`installers/`、`hermes-home.ts`）；`tests/` 含 `assert-no-secrets.ts`、`secret-outputs.test.ts`、`install-script-ps1.test.ts`。

**Skills / Livewares 托管**：此仓库是两棵树的 canonical host，经 GitHub raw 由 `OFFICIAL_SKILLS_BASE` 拉取并钉在 `skills-vX.Y.Z` tag：`skills/{hermes,openclaw,shared}/*/SKILL.md` + `skills/manifest.json`，以及 `livewares/` + `livewares/manifest.json`。根 `package.json` 有 `skills:manifest` / `skills:check` / `livewares:check` / `skill-frontmatter.test.mjs` / `release-guard.test.mjs` —— **与本项目「命令面清单由 `build_parser()` 生成 + `test_skill_manifest.py` 守漂移」是同一套治理思路**。

**协议文档**：`docs/agent-protocol.md` 声明权威来自服务端 REST + WS v2 contract，提取自 `@clawling/clawchat-plugin-openclaw` v2026.7.29-3；鉴权是 invite-redeemed bearer JWT（**无签名/HMAC/client secret**）；`examples/cloud-agent/` 是 ~650 行零依赖探针。文档明确划分「通道（channel）」与「宿主（host）」职责，并强调 **`hello-ok` / auto-greeting 只证明通道连上，不证明宿主模型接上** —— 与本项目「消息可见 ≠ 接单」完全同构。

**风险点**：`--dangerously-force-unsafe-install` 绕过宿主安装期安全扫描；`--profile` 与 `HERMES_HOME` **不可同时给**，否则凭据落到 `…/profiles/<name>/profiles/<name>`；`hermes profile create` 不切换当前会话。这些都是它自己文档里写明的坑。

**发布**：tag `skills-v1.19.0` / `skills-v1.18.1` / `install-2026.10.24-1`；最新 commit `cadd1995`（2026-10-31T17:07:38Z，atomic state-file writes / write-point gate / credential output checks）。

## 4. 与本项目既有实现的关系

| 项目内文件 | 现有协议/定位 | 与官方 v2 的关系 |
|---|---|---|
| `src/.../coordination/clawchat_transport.py` | 本地 **TCP + NDJSON** envelope，`execute=True` 才连，opt-in、依赖自由 | **自研简化子集**，不是 v2 客户端 |
| `cli.py: coord-clawchat` | 预览 / 单条发送，默认不 execute | 调上面的 transport，同样不兼容 v2 |
| `docs/CLAWLING_INTEGRATION.md` | 本机 ClawChat GUI 机器通道，`app_version=1.0.30+208`、**protocol version=1**、HTTP `/v1/send`、连接码 + roster、发现文件 `%APPDATA%\com.newbaselab\clawchat\machine_channel.json` | **v1 桌面应用**，与官方 v2 插件不同代 |
| `docs/CLAWCHAT_EVALUATION.md` | 评估的是**同名不同项目**：`cbd/clawchat`→Cowchat（本地 socket/NDJSON）、`ngmaloney/clawchat`（Electron 前端）、`alexrudloff/clawchat`（P2P） | **不是 `clawling` 组织**，结论不可直接套用 |
| `scripts/clawling-register.ps1`、`wake_adapters/hermes_desktop.ps1`、`coordination/realtime_hub.py`、`bridge.py` | 本机注册 / 桌面唤醒 / 实时通道 | 与官方插件的 Hermes ABI 注册不是一回事 |

⇒ **不能直接把 `coord-clawchat` 当成官方 v2 客户端**，也不能把评估文档里的 Cowchat 结论当成这个组织的结论。若将来真要接 v2，需要先做协议映射与 envelope 兼容层，并重新走「证据链 + `VERIFIED` 关单」的验收。

## 5. 为什么不引入运行时（硬约束对照）

| 项目硬约束 | 官方插件/安装器要求 | 判定 |
|---|---|---|
| 库级无服务、无 DB、无后台线程 | 常驻 WebSocket 连接 + 第三方服务端 + 宿主 Hermes/OpenClaw 进程 | ❌ |
| 不新增 schema / `coord-*` 命令面 | 55 个 `clawchat_*` 工具、7 个 hooks、plugin.yaml、skills manifest | ❌ |
| 判断仍是规则可复算、fail-closed | 服务端持有 wire contract；`--dangerously-force-unsafe-install` 绕过宿主安全扫描 | ❌ |
| 单次有界、默认 dry-run | 插件是常驻 gateway，installer 会改写宿主配置 | ❌ |
| 只引协议不引运行时 | `agent-protocol.md` 的通道/宿主划分、具名错误目录、凭据边界 | ✅ 可引思路 |

## 6. 可以借鉴的具体能力（只引协议与工程规范）

| 官方文件 | 可借鉴点 | 项目对应位置 |
|---|---|---|
| `skills/*/SKILL.md` + `skills/manifest.json`、`skills:check` / `skill-frontmatter.test.mjs` | 能力卡 + 生成 manifest + 守线测试 | `skills/README.md`、`coordination/skill_manifest.py`、`tests/test_skill_manifest.py` |
| `clawchat_gateway/atomic_file.py`、`packages/core/src/fs/atomic-write.ts` | 原子写（`x` 打开 + `os.replace` + fsync） | `ui_fallback._atomic()` 已是同形态 |
| `gate_outcome.py`、`fixtures/permission_events/*.json` | 具名拒绝 + 固定样本，不靠布尔 | `delivery_verdict()`、`action_verification.verdict()` |
| `docs/reference/prompt-injection.md`、`tool-grants.md` | 外部内容不可当授权、工具需显式授予 | `docs/AGENT_OPERATION_GUIDE.zh-CN.md`、`docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md` |
| `docs/instance-locks-and-install-conflicts.md`、`multiplex-profile-isolation.md` | 单实例锁、profile 隔离、凭据保留 | `desktop-input.lock`、`session_fingerprint` |
| `secret-outputs.test.ts`、`assert-no-secrets.ts` | 守线断言「令牌绝不出现在输出」 | `docs/CLAWLING_INTEGRATION.md` 的令牌纪律，可补自动守线 |
| `docs/agent-protocol.md` 的 channel/host 划分与「`hello-ok` 只证明通道」 | 「连上 ≠ 接单」 | `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md` 的 `MESSAGE_VISIBLE` 口径 |
| `group_message_coalescer.py`、`group_command_receipt.py` | 组内消息合并与命令回执 | `campaign_cycle.py`、`transcript_bus.py` |

## 7. 本次调研暴露的「错误情报」样本

1. **数字漂移**：README 53 个工具 vs `tools.md` 55 vs `plugin.yaml` **55**。散文数字不可信，声明文件才权威。
2. **同名混淆**：`CLAWCHAT_EVALUATION.md` 里的 Cowchat / Electron 前端 / P2P 三个「ClawChat」都不是 `clawling` 组织的项目，名称相同 ≠ 同一依赖。
3. **代差混淆**：本机 GUI v1 HTTP、项目 NDJSON/TCP、官方 v2 WebSocket 是三个不同协议面。
4. **连通性 ≠ 能力**：官方 `agent-protocol.md` 自己写 `hello-ok` 只证明通道，不证明宿主模型在回答——与本项目「只有 `VERIFIED` 能关单」一致。

这些正是项目「观点需要实据、避免团队受错误情报影响」的具体注脚：来源数量不等于独立证据数，标题与 README 散文不等于声明文件。

## 8. 明确未做

- 没有 clone、install、`hermes plugins install`、`npx`、注册连接码或连接任何 ClawChat 服务端；
- 没有修改宿主配置、没有新增依赖、没有新增 schema、没有新增 `coord-*` 命令面；
- 没有把本项目接到官方 v2，也**不宣称已集成 / 已兼容 / 已部署**；
- 没有改 `clawchat_transport.py` 或 `docs/CLAWLING_INTEGRATION.md` / `CLAWCHAT_EVALUATION.md` 的既有结论（仅在评估文档顶部加了指向本文的提示）；
- 没有承诺 7x24 无人值守，没有追踪积分/额度。

## 附录 A：原始数据快照（2026-11-06）

```json
{"org":{"login":"clawling","type":"Organization","name":"CLAWLING PTE.LTD.","public_repos":2,"followers":45,"created_at":"2026-04-08T07:18:36Z","updated_at":"2026-08-04T05:56:54Z"}}
{"repo":"clawchat-plugin-hermes-agent","stars":29,"forks":3,"issues":5,"size_kb":3967,"license":"MIT","branch":"main","created_at":"2026-05-28T10:42:46Z","pushed_at":"2026-11-04T11:40:55Z","topics":[],"fork":false,"archived":false}
{"repo":"clawchat-plugin-install-cli","stars":2,"forks":0,"issues":3,"size_kb":1133,"license":"MIT","branch":"main","created_at":"2026-05-28T10:44:46Z","pushed_at":"2026-11-03T04:15:56Z","topics":[],"fork":false,"archived":false}
{"plugin.yaml":{"manifest_version":1,"name":"clawchat","kind":"platform","version":"0.14.0-125","provides_hooks":7,"provides_tools":55}}
{"pyproject.toml":{"requires-python":">=3.11","dependencies":["websockets>=13,<16"],"package":false}}
{"hermes_commit":"d18f8b1c 2026-11-04T03:53:52Z Merge pull request #79 from clawling/pre-release"}
{"installer_commit":"cadd1995 2026-10-31T17:07:38Z fix(core): atomic state-file writes, write-point gate, and credential output checks (#36)"}
```
