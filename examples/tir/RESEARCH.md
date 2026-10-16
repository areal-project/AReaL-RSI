# TIR 多轮训练研究

`pipeline.py` 保留最小独立端到端例子；`study.py` 管理真实数据上的分阶段研究，
`rsi.py` 才是实际 `/rsi` 的例子入口。训练并不是 `/rsi` 的必选步骤。
其他任务可以只优化 harness，或调用同一套 infra 服务组织自己的训练过程。

公共 `JobExecutor` 执行进程并处理失败/取消，`EvaluationService` 检查完整覆盖并保存轨迹，
`AReaLBackend` 校验真实训练步数和 checkpoint，`LocalTrajectoryStore` 保存轨迹索引。
数据切分、题目合成、配比、课程、轮次、选择与统计检验属于 TIR example。
Slurm 在本例中装配到公共 JobSpec 命令，AReaL worker 在 allocation 内使用 local scheduler。

## 数据与实验协议

GSM8K 官方 train 确定性抽出 256 题 dev，其余作为训练池。官方 test 确定性抽出
512 题 final。按规范化问题的哈希去重，从训练池排除全部官方 test 和 dev 身份。
任何生成内容都不包含 held-out 题的改写。参考解仅保留在训练数据记录中，worker
传给模型的输入只有 prompt；answer 只传给 reward，参考解不传给 rollout。

合成代码 `synthesis.py` 提供库存、速率、预算、比例和递推五类题，约束数值范围和语义，
用独立计算交叉验证答案；混合真实训练题，确定性抽样、去重、限制重试次数。
DataRecipe 控制真实/合成数量、真实题最少推理步数、题型权重、合成难度和随机种子。
不把目前这份初始策略当作最终研究结果：Leader 需要根据 dev 失败模式修改/验证它。

prepare 冻结数据哈希、harness、reward、worker、数据读入和后端代码、基模型、推理预算。
源码快照包含未提交文件和完整归档；每个 GPU 阶段前后校验源码，运行阶段不能同时改代码。
数据策略与训练 recipe 可在阶段之间迭代，每轮保存假设、配方、源码、生成数据与父模型。
模型 warm start，但每轮优化器重新初始化。训练检查所需步数，最后一步才保存 checkpoint。
阶段使用文件锁保证单个写入者；失败记录保留，成功的评测不重复提交；训练失败用新轮次名，
训练已完成但 dev 失败时可单独重试 evaluate，使用独立 attempt 名。

至少完成两轮训练，通过 dev 选最好的候选，selection 一经保存不再调参。最终阶段独立
运行基模型和选定模型，对齐逐题结果，报告净增益、wins/losses、固定种子配对 bootstrap
95% 区间和 exact McNemar p。目标 +3pp，是否显著另行报告，不能把区间含零说成显著提升。
512 题的区间可能较宽；目标没有达成时应如实报告，不能反复用同一 final 调参。
本例的 held-out 限制依赖研究规范和哈希校验，不宣称同一 Unix 用户下有恶意访问隔离。

## 运行

以下命令从项目根目录执行。准备 parquet 的可选依赖 pyarrow 位于 AReaL 镜像中，
不需要装进 RSI 宿主环境。CPU 数据准备也通过 Slurm 执行。

```bash
python -m examples.tir.benchmark --source /storage/openpsi/data/gsm8k/main --output /absolute/benchmark
python -m examples.tir.study --study /absolute/study prepare --config examples/tir/study.json
python -m examples.tir.study --study /absolute/study evaluate --candidate base
python -m examples.tir.rsi --study /absolute/study --env-file /absolute/private.env
```

study.json 中的 benchmark 路径指向第一步产物。study 自动申请 rsi reservation 的 2 GPU、
16 CPU、128G，阶段结束即释放，不会在 Leader 分析时占住 GPU。
最后一个命令使用真实 ResearchSystem 和模型 API：Interaction 整理目标并交接 Leader，
Leader 修改合成代码/数据配方、多轮训练与分析，然后提交 decision.json；宿主独立冻结选择
并运行 final。会话、角色迁移、工具和原生历史保存在 study/rsi，凭据由环境读取，不能写入产物。
原生 Codex CLI 需要 >=0.162.1。已有中断会话使用同一 study 加 `--resume`，不要另建会话。

无需启动 RSI 也可手动执行 `study round --name r1 --recipe examples/tir/recipes/r1.json`。
这用于开发阶段检查，不能作为“真实 RSI 驱动研究”的证据。
