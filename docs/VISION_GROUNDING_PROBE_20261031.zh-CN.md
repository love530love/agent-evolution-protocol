# 视觉定位真跑验证（2026-10-31）

预注册在 `docs/MAGE_VL_COWORK_ASSESSMENT_20261031.zh-CN.md` 的那个判决点，这一轮真的跑了。
结论一句话：**这道闸门能拦住不合法的框，但拦不住自信的错误框——12 格里 4 格通过闸门，只有 1 格是对的。**

## 1. 实验设置

- **模型**：`Qwen/Qwen2.5-VL-3B-Instruct`，bf16，`transformers 4.51.3` + `torch 2.10`，
  RTX 3090 **24 GB**（`device_map="cuda"`，无 SGLang）。权重 7.1 GB 从 ModelScope 取得
  （HuggingFace 直连实测 ~150 KB/s，ModelScope ~11 MB/s）。
  **不是** Mage-VL-Cowork：Mage 要 SGLang 和它自己的权重，本轮没跑，所以这里任何一个数字都
  **不能**算到 Mage 头上。
- **窗口**：本机三个真实客户端窗口，DPI 感知后按物理像素抓取
  （`SetProcessDpiAwareness(2)`；本机 150% 缩放，3840×2160 物理）。
  - Qoder 3862×2110（提权进程，Electron，UIA 无输入控件）
  - ChatGPT 1920×1230
  - WorkBuddy 1932×2088
- **两种坐标格式**：仓库 `vision_grounding.GROUNDING_PROMPT` 的 `[0,999]` 网格；
  以及绝对像素（Qwen2.5-VL 训练时用的格式）。
- **两种说法**：`composer`（术语）；`the message input box at the bottom of the window where a
  user types a message`（人话）。
- **独立参照**：不用模型自己的置信度。
  - 主要参照：`coordination/ui_profiles/qoder.json` 里**人标定**的 `composer_x_ratio: 0.37` /
    `composer_y_ratio: 0.92` → Qoder 窗口上 (1429, 1941)。
  - 交叉验证：Windows OCR（`windows_ocr_compat.ps1`，`zh-Hans-CN`）的行框，
    取「落在模型给出的框内」的 OCR 文本，看它到底框住了什么。

## 2. 12 格结果

| 格式 | 说法 | Qoder | ChatGPT | WorkBuddy |
|---|---|---|---|---|
| 网格 0-999 | `composer` | 拒（越界） | 拒（出窗） | 拒（越界） |
| 网格 0-999 | 人话 | 拒（越界） | 拒（越界） | 拒（越界） |
| 像素 | `composer` | **通过**（错） | **通过**（错） | **通过**（错） |
| 像素 | 人话 | **通过（对）** | 拒（出窗） | 拒（出窗） |

`通过` = `admissible()` 返回 True；`拒` = 返回 False 并给出理由。

### 通过闸门的那 4 格，OCR 说了什么

| 窗口 | 模型给的框 | 框内的 OCR 文本（节选） | 判定 |
|---|---|---|---|
| Qoder | 145,197 1000×1000 | `BETA`、`专家．技能．连接器`、`agent-evolution-protocol`、`升级多 agent 协作唤醒机制` | 左侧栏，**错** |
| ChatGPT | 456,18 764×192 | `mrexodia/ida-pro-mcp`、`https://github.com/mrexodia/ida-pro-mcp` | 对话顶部，**错** |
| WorkBuddy | 145,167 188×188 | `写入输入框`、`感知变化` | 右栏表格单元格，**错** |
| Qoder | 945,1870 1648×200 | `天帮你做些什么？ @ 添加上下文， / 调用技能与指令`、`Deepseek-V4.1-Flash` | **输入框本体，对** |

第四行是唯一对的：它同时满足 `contains_calibrated_point: true`（人标定点 (1429,1941) 落在框内）
和 OCR 文本命中输入框占位符。两个独立判据一致，这条才算证据。

## 3. 三个结论

### 3.1 闸门管合法性，不管对错

`admissible()` 只回答「框在窗口内、置信度够」。像素+`composer` 那 3 格置信度都是 **0.99~1.0**，
全部落在窗口内，全部是错的。**如果拿 `admissible` 当「可以点」，四次里会点错三次。**

这正好印证 v0.6.7 里 `input_routing` 把 `vision` 的 `needs_verification` 定成 `True`、
`delivery_verdict` 默认 defer 的选择。现在这条不是设计口味，是实测数据。

### 3.2 提示词格式比模型本身影响更大

同一个模型、同一张图：

- 网格格式 **6/6** 全部越界（模型答 `width=1021`、`height=1000`，它把网格当成像素了）→ 全拒。
- 像素格式 4/12 通过、1/12 正确。

也就是说，仓库里给 Mage 写的那份 `[0,999]` 提示词，换到这个模型上**系统性不可用**。
不是「偶尔偏一点」，是坐标空间根本不对。任何「换个小模型顶上」的打算都必须先测格式。

### 3.3 术语是第二道坎

像素格式下，只说 `composer` 3/3 全错；换成人话，Qoder 那格才对。
一个 3B 模型不认识 `composer` 这个 UI 术语，它会把「composer」关联到别的东西。
这不奇怪，但值得写下来：**提示词里的名词是被模型解释的，不是被它查表的。**

## 4. 许可证（顺带查出来的）

`Qwen2.5-VL-3B-Instruct` 的 LICENSE 是 **Qwen RESEARCH LICENSE AGREEMENT**，
不是 Apache-2.0（2026-10-31 从模型卡核实）。所以无论准确率如何，它都不能当生产 grounder。
这条写进了 `examples/execution-provider-catalog.json` 的 `license_note`。

## 5. 这一轮在仓库里留下的东西

- `qwen2_5_vl_3b` 登记为 `desktop_visual` / `vision-service` / `candidate`，
  `limitations` 里是上面这些实测数字，`status` **没有**晋升——因为只有一格对，失败不是晋升。
- `mage_vl_cowork` 仍然 `candidate`，本轮没有给它增加任何证据。
- 探针脚本在 `.tools/vision-probe/`（gitignored）：`capture.py`、`ground.py`、`summarize.py`，
  原始证据在 `.tools/vision-probe/out/`（PNG、OCR JSON、`summary.json`）。

## 6. 没做的

- **Mage 本身没跑**。24 GB 卡按 `docs/MAGE_VL_COWORK_ASSESSMENT_20261031.zh-CN.md` 的显存推算
  是「舒服」档位，但 SGLang + 9.48 GB 权重 + `llava_onevision2` 的自定义代码这一整套本轮没装。
  预注册的四步探针里，只有第 2 步（不一致率）在**另一个模型**上做了。
- **只有一个窗口有独立参照**。ChatGPT/WorkBuddy 没有标定过的 `composer_*_ratio`，
  所以它们的「错」是靠 OCR 文本内容判定的，不是靠坐标比对。要给它们也算不一致率，
  得先补 profile 标定。
- **没有多轮重采样**。`do_sample=False`，每格一个答案；没测模型在同一格上的稳定性。
