# 本地模型安装

仓库不提交模型权重。开发机可执行：

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\install-model.ps1
```

默认下载 `Qwen/Qwen3-0.6B` 到 `.models/Qwen3-0.6B`。该模型为 Apache-2.0，来源为 [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)。需要更强模型时可指定 `-Model Qwen/Qwen3-4B-Instruct-2507` 和独立目录。权重目录已加入 `.gitignore`，安装器会写入本地 `AEP-MODEL-MANIFEST.json` 记录来源、许可和下载时间。

模型只用于意图理解和动作候选生成；必须经过 `model_contract` 与 `capability_bridge`，再由既有 UIA/CDP/MCP 适配器执行。

下载后可验收：

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\model-status.ps1
```
