# 本地小模型与 Computer-use 基底

## 结论

AEP 现在提供统一的 `coord-capability-plan` 规划入口。它把“理解任务”和“操作电脑”分开：模型只能产生受约束的候选意图；AEP 根据能力清单选择 UIA、CDP、MCP、浏览器或文件信箱，并要求租约、原窗口锚点、回读证据和失败止损。弱 Agent 不需要自带 computer-use，也能调用同一基底；但“开放”不等于绕过权限或无审计的任意执行。

## 推荐的可选模型

默认候选是 Apache-2.0 的 Qwen3-4B-Instruct-2507。它适合本地做意图分类、任务拆解、候选动作排序和重试解释，不应直接获得任意 shell 或桌面输入权限。可用 Transformers、vLLM、SGLang 或其他本地运行器；AEP 不强制安装模型，机器没有模型时仍使用规则路由和文件接力。

若机器资源更紧张，可换用同一接口的更小指令模型。模型更换不改变能力协议：输出必须是结构化 JSON，包含 `intent`、`operation`、`target`、`confidence` 和 `reason`，未知字段与任意命令字符串一律拒绝。

## 运行器与工具层

`llama.cpp` 可作为跨平台、无云依赖的可选运行器；也可以接现有 Transformers/vLLM/SGLang 服务。桌面执行按可靠性排序：原生 Agent/浏览器工具 → 已授权 CDP → UI Automation → 已探测的 MCP provider → OCR/剪贴板人工确认 → 文件信箱。每一层必须声明或探测能力，声明不等于已验证。

```powershell
python -m agent_evolution_protocol.cli coord-capability-plan `
  --intent "读取 WorkBuddy 原会话的最新回执" --operation read `
  --manifest examples/capabilities.json
```

高风险 `send`/`click` 默认只返回 `REQUIRES_EXPLICIT_LEASE`；通过共享桌面输入租约、唯一窗口锚点和事件 ID 后，才交给现有 commander/uia/cdp 适配器执行，再通过 `MESSAGE_VISIBLE`/`AGENT_ACK` 等状态确认，而不是把“已输入”当作成功。

## 失效与引导

没有可用 provider 时，规划器返回 `NO_CAPABILITY`，Agent 应执行 `aep route`、`aep coord-desktop inventory`、`aep coord-onboarding`，随后把缺失能力、所需权限和下一步写入协作区。模型无响应时由 wake daemon 使用显式队列唤醒；静默期间不轮询模型。

## 依据

Qwen3-4B-Instruct-2507 的模型卡标注 Apache-2.0 并提供 Transformers、vLLM、SGLang 用法：[Qwen 模型卡](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)。`llama.cpp` 是 MIT 许可的跨平台 C/C++ 推理项目：[llama.cpp](https://github.com/ggml-org/llama.cpp)。
