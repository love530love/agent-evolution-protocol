# Windows 本机构建经验索引（证据优先）

给所有协作 Agent 的短入口：先查具体案例，再做只读环境盘点，最后在**当前项目**验证。下列是本机文章中的历史实战记录，不是对任意新版依赖、GPU 或工具链的兼容性保证；不要复制文章中的固定盘符、版本号、注册表修改或源码补丁直接执行。

## 先分流，不重复踩坑

| 症状/任务 | 先读的本机原文 | 已记录的方法与边界 |
| --- | --- | --- |
| 编译目录太深、`nvcc`/`cl` 无法写 `.obj` | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\MMCV预防性短路径编译策略-159988324.md`；`E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\我们如何在Windows上成功编译MMCVCUDA算子-159985290.md` | `subst` 映射**源码根**到短盘符，连同构建/临时目录缩短；仅缩 CUDA 目录不够。先检查拟用盘符空闲及映射对象，再建立映射；只在本轮进程环境设构建变量。映射盘符约 3–4 字符，但深层后缀仍可能长。不要删除已有映射。 |
| CUDA/cuDNN 多版本共存 | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\Windows多版本CUDAcuDNN环境配置完全指南-158812062.md`；`E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\WindowsCMD多版本CUDAcuDNN一键切换管理方案-160522075.md` | 让当前构建进程的 `CUDA_HOME`、`CUDA_PATH`、`PATH` 首个 `nvcc`、cuDNN 路径及项目期望版本一致；切换后检查真实 `nvcc`、PyTorch CUDA、CMake 缓存。文章的版本配对和注册表变量是历史机器状态，需重新核验，不默认全局改 PATH。CMD 批处理与 PowerShell 语法不可混用。 |
| `cl.exe` 缺失、MSVC/CMake 选错 | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\硬核指南在Windows环境下从源码编译Pynini含Wheel复用技巧-160551026.md`；`K:\PythonProjects5\Unlimited-OCR\复盘总结\第1篇-从判定不可行到原生编译成功.md` | 先确定 VS 实际安装、目标架构和 Developer 环境，再查 `cl.exe` 所在目录、`nvcc` 与 CMake 生成器。`vcvarsall.bat` 是 CMD 批处理；在 PowerShell 中直接运行不能把环境变量带回父进程。若生成器变更，须处理**特定构建目录**的旧缓存，不能把失败归因于源码。 |
| OpenFST/Pynini Windows wheel | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\硬核指南在Windows环境下从源码编译Pynini含Wheel复用技巧-160551026.md` | 案例使用 Windows 适配分支、VS 2019/2022、Python 3.12，先备依赖后 `pip wheel . --no-build-isolation`；生成 wheel 后再安装并用 `pynini.cross`/`compose` 做功能验证。`--no-build-isolation` 只适合已完整准备且记录版本的环境；wheel 的 Python ABI/平台标签必须匹配目标环境。 |
| PyTorch/CUDA 扩展在 MSVC 下语法冲突 | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\突破Windows编译禁区BitNet1-bitLLM推理框架GPU加速部署编译BitNetCUDA-157975315.md` | 历史案例记录 `uint`、bf16 转换、预处理器、头文件冲突的逐层定位；构建产物需做导入和实际 CUDA 算子调用验证。文中的宏屏蔽 PyTorch 头文件、`setup.py install`、固定 sm/版本属于高风险案例特解，**不得作为通用补丁**。 |
| MMCV CUDA 扩展 | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\我们如何在Windows上成功编译MMCVCUDA算子-159985290.md` | 锁定 torch/CUDA/MSVC、短路径、单一目标 Python，构建后分别验 `mmcv.__file__`、`mmcv._ext`、所需 ops、图像 I/O；wheel 与依赖另存。`Config` 在该案例的 MMCV 2.x 中来自 `mmengine.config`，顶层导入失败不等于编译失败。不要混装 `mmcv`/`mmcv-full` 后直接凭 `import mmcv` 判定成功。 |
| 没有 Windows 官方 wheel 的 GPU 项目 | `K:\PythonProjects5\Unlimited-OCR\复盘总结\第1篇-从判定不可行到原生编译成功.md` | 先拆主包、平台扩展、JIT 依赖，区分“未发布 wheel”与“无法原生编译”；按环境→GCC 方言/MSVC 语义→目标架构裁剪→链接的顺序处理。案例产出 `sglang-kernel` wheel 且导入成功；但 `--no-deps` 绕过了版本精确依赖，只能在 ABI/API 兼容已独立验证时权衡使用。 |
| 项目 Python/uv 路径漂移 | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\2026年终极防坑指南基于EPGF架构彻底本地化UV环境与工具-161887090.md`；`E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\EPGF新手教程11在PyCharm中文版GUI中创建uv环境并把uv做到项目自包含工具本地化为必做-156644532.md` | EPGF 的实践意图是项目 `.venv` 与显式解释器路径、工具路径，避免调用全局 `python`/`uv`。跨盘硬链接警告可考虑 copy 模式；老式构建脚本需要 pip/setuptools 时可选择 seed。文章的 `uv` 参数应先用当前 `uv --help` 核对，不能假设所有版本相同。`uv` 装入 `.venv` 是该架构的选择，不是所有 uv 项目的必需条件。 |
| Go/CGO 报 `gcc not found` | `E:\Downloads\Compressed\gitcode_knowledge-main20260919\gitcode_knowledge-main\CSDN博文备份\Windows下GCC编译器安装与排错实录-157875611.md` | 案例使用 MSYS2 MinGW64 GCC，并遇到 pacman 包版本冲突；先更新 MSYS2，再核对 `gcc.exe` 真正目录与当前 shell 的 PATH。不要把 MSYS2 GCC 与 MSVC 编译链混为一谈；不同 ABI 目标需单独验证。 |

## 本机复用约束

1. 默认 PowerShell 7。复制历史 CMD 命令前先辨认语法：`set VAR=...`、`cd /d`、`where`、`call vcvarsall.bat`、`%VAR%` 不可原样当 PowerShell 写法。需要 CMD 批处理时仅以明确的 `cmd /c` 子进程执行，并记录为什么需要 CMD；主流程仍保持 PowerShell 7。
2. 每次报错附上**确切命令、shell、工作目录、所用 python/uv/nvcc/cl 的绝对路径和版本、退出码、首次真实错误行**。不要只传最后一屏堆栈，避免其他 Agent 重试同一坑。
3. 编译前只读核对项目 `.venv\Scripts\python.exe`、`pyproject.toml`/锁文件、`torch.version.cuda`、`nvcc --version`、VS 环境、GPU 架构、构建目录长度。要改环境，优先限于本进程/本项目；不要为一次构建全局改系统 PATH。
4. `subst` 是这台 Windows 长路径编译的优先解：先 `subst` 列出现有映射、确认目标目录与空闲盘符，再映射**具体源码根**；编译后仅解除**本次自己建立**的映射。`subst` 缩短命令路径，不修复 ABI、版本或权限问题。
5. 验收至少三层：产生 wheel/`.pyd`；目标解释器导入且路径正确；实际功能或 CUDA kernel 在目标 GPU 上运行。仅文件存在或返回非零张量，不足以证明数值正确；必要时与参照比对。

这些文档是索引，不复制大篇原文。发现新坑时应附原始失败证据、最小修复、成功环境和反例，再提炼为新条目；未经复验的推断须标“待验证”。
