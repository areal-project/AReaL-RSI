# 产物研究

产物优化统一使用 ResearchApp / ResearchEngine，不再注册 ArtifactEvolutionWorkflow。

ResearchConfig 提供初始目录（可选）、TaskBrief、执行命令和可信验证器。研究员使用工具修改工作区，save_candidate 冻结产物，verify_candidate 运行验证。通过报告和 RSI 后续派单持续改进，不要求每个任务都生成产物。

完整可运行示例见 [KernelBench 产物研究](../kernelbench_artifacts/README.md)，通用接口见[研究与执行接口](../../docs/interfaces/README.md)。
