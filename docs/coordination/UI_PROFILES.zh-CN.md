# UI Profile：哪些事实可以分发，哪些必须留在本机

> 目标读者：换一台机器要接管桌面客户端的人，或者要给新客户端加 profile 的人。
> 相关：`docs/EXECUTION_PLANE_UPGRADE_PLAN.zh-CN.md`（模板化安装器的原始要求）、
> `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md`（投递与插话的操作面）。

## 0. 一句话

`coordination/ui_profiles/` **不入库**，`examples/ui_profiles/` **入库**。
前者是运行态（本机路径 + 会话标题 + 探测时的 pid），后者是模板（可移植的客户端事实 + 两个占位符）。

## 1. 一个 profile 里混着两类事实

| 类别 | 例子 | 是否入库 | 理由 |
|---|---|---|---|
| **客户端事实** | 输入框是哪个控件、`send_mode`、`interject_hints`、插话控件是否「发送后才出现」、`ocr_language`、`busy_markers` | ✅ 入 `examples/` | 这是测出来的知识，换机器依然成立，而且很贵 |
| **本机事实** | `exe` 绝对路径、`session_marker`（只属于目标会话的那句话）、探测时的 pid | ❌ 只留本机 | 公开仓库里这些是**披露**，不是移植性修复 |

`exe` 这条尤其不能抄：它**随客户端版本漂移**。`chatgpt.json` 的路径里嵌着 Store 包版本号，
每次应用更新就失效一次；`qoder.json` 曾经因为钉死版本号而从 0.5.1 漂到 0.5.3，
导致窗口明明在屏幕上却被判成「找不到窗口」。**从运行中的进程读，不要猜一个看起来更新的号。**

## 2. 从模板实例化（三步）

```bash
# 1. 复制模板
mkdir -p coordination/ui_profiles
cp examples/ui_profiles/qoder_cn.json coordination/ui_profiles/qoder_cn.json

# 2. 填两个字段：exe、session_marker（模板里写着 <REPLACE: ...>）
#    exe            <- 从运行中的进程读，别照抄安装目录
#    session_marker <- 只在该会话里出现的一句话，绝不能是通用词

# 3. 验证
aep coord-uia observe --agent qoder_cn
```

第 3 步的判据不是「跑通了」，而是回执里这三项：

| 字段 | 期望 | 不满足时说明什么 |
|---|---|---|
| `path_verified` | `true` | `exe` 填错了，或填的是旧版本 |
| `session_marker_check` | `seen` | `session_marker` 不是这个会话独有的，或打开的会话不对 |
| `input_selected_reason` | `composer-hint` | 输入框是靠几何碰运气选中的，`composer_hint` 该重新标定 |

**两个占位符都是 fail-closed 的**，这是刻意的：

- `exe` 不是绝对路径 → `load_profile` 直接抛错，模板不可能被误用；
- `session_marker` 匹配不到 → `SESSION_MARKER_MISSING`，拒绝往一个无法识别的会话里打字。

## 3. 各客户端的可移植事实（含证据等级）

「证据」一栏是必要的：把「我操作失败」记成「客户端不具备该能力」这件事，
本项目已经犯过一次（见 `docs/releases/v0.6.16.md` 第 1 节）。

| agent | transport | 输入框 hint | 发送 | 忙碌插话 | 证据 |
|---|---|---|---|---|---|
| `chatgpt` | uia | `和 Codex 一起工作` | enter | 未测 | 输入框 hint 实测；窗口里同时有一个 Markdown 编辑器带同族占位符，靠几何过滤挡掉 |
| `qoder` | uia | `长按鼠标语音输入文字` | enter | ✅ `插话`，发送后才出现 | **直接实测**：插话后模型回了探针 token，并说明原任务已在同一回合交付完 |
| `qoder_cn` | uia | `发送任务消息` | enter | ✅ `插话`，发送后才出现 | **直接实测**：点击后转写 318→340 行，状态变「考虑不同角度…」 |
| `codebuddy_cn` | ocr | `输入消息...` | enter | ⚠️ 未复测 | `allow_busy_interject: true` 但**没有** `interject_after_send`，仍走严格闸门：忙碌时会被拒。拒是安全的，但可能是同一个假阴性 |
| `workbuddy3` | ocr | `今天帮你做些什么` | enter | 未测 | 该客户端没有 UIA 树，OCR 是唯一通路 |

### `interject_after_send` 是什么意思

Qoder 0.5.3 两个构建都是：**忙碌时消息能发出去进队列，「插话」按钮在消息发出之后才出现**，
点它才把排队消息送进正在跑的回合。

所以「准入」需要**两半同时成立**：

1. 适配器观察：客户端确实在忙，且 profile 点名的控件此刻**不在**屏幕上（`appears_after_send`）；
2. profile 声明：`interject_after_send: true`（这个客户端的控件出现得晚）。

只有 hint 是猜的 profile **过不去**——这正是 `codebuddy_cn` 保持严格闸门的原因。
要给它加 `interject_after_send`，先按下面的方法实测。

### 怎么实测一个客户端的插话能力（别再犯同一个错）

```
粘贴（真实输入事件，不是 ValuePattern.SetValue）
  → 强制回车 1~2 次
  → 输入框清空、发送消息 变成 停止生成、出现「插话」Button
  → 点击「插话」
```

**`ValuePattern.SetValue` 只改 DOM，不触发 web app 的 input event。** 用它预置文字，
应用仍认为输入框是空的，回车被静默吞掉，消息从未进队列 —— 于是缺席的「插话」控件
会被读成「这个客户端没有插话通道」。这个错误已经写进过一次 release。

## 4. 模板怎么再生

模板由脚本从本机 profile 生成，不是手抄：

```bash
python scripts/export_ui_profile_examples.py          # 重新生成
python scripts/export_ui_profile_examples.py --check   # 与 profile 不一致时退出码 1
```

脚本做的事，逐条都能核：

- `exe` / `session_marker` → 占位符；
- 所有 `pid <数字>` → `pid <local>`（「不含 PID」是 `EXECUTION_PLANE_UPGRADE_PLAN` 的明确要求）；
- 丢掉 `exe_note` 与 `evidence`（一次性探测记录，不是配置）；
- 其余字段原样保留。

`tests/test_ui_profile_examples.py` 守着三件事：模板是合法 JSON、占位符还在、
**没有把本机路径 / 会话标题 / pid 带出去**。第三条是隐私回归测试，不是格式检查。

## 5. 仍然没做的

- 模板里没有安装器。`EXECUTION_PLANE_UPGRADE_PLAN` 要的是「由安装器在目标机器重新探测并生成锚点」，
  现在只有手工三步 + 一次 `observe` 验证。
- `chatgpt` / `workbuddy3` 的忙碌插话未测。
- `codebuddy_cn` 的插话未复测（见上表）。
