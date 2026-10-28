# CUDA 本地模型运行

本机已检测到 NVIDIA RTX 3090（24GB）。安装器只负责下载权重；使用 `scripts/run-local-model.ps1` 时会强制要求 CUDA 可用，拒绝静默回退 CPU：

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\run-local-model.ps1
```

首次运行需要可选依赖 `pip install torch transformers`，应根据本机 CUDA 版本选择官方 PyTorch wheel。模型仅产生结构化协作建议，仍须经过 AEP 的模型契约与能力桥接层。
