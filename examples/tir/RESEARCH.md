# TIR 多轮训练研究

`pipeline.py` 保留最小独立端到端例子；`study.py` 管理真实数据上的分阶段研究，
`rsi.py` 才是实际 `/rsi` 的例子入口。训练并不是 `/rsi` 的必选步骤。
其他任务可以只优化 harness，或调用同一套 infra 服务组织自己的训练过程。

公共 `JobExecutor` 执行进程并处理失败/取消，`EvaluationService` 检查完整覆盖并保存轨迹，
`AReaLBackend` 校验真实训练步数和 checkpoint，`LocalTrajectoryStore` 保存轨迹索引。
数据切分、题目合成、配比、课程、轮次、选择与统计检验属于 TIR example。
Slurm 在本例中装配到公共 JobSpec 命令，AReaL worker 在 allocation 内使用 local scheduler。

## 数据与实验协议

默认采用 AReaL TIR 示例配置对应的 ToRL 数据，评分复用 AReaL 的
`get_math_verify_worker().verify`，支持分数、根式等数学答案。官方来源为
https://github.com/GAIR-NLP/ToRL/tree/1db091d9cbd37df493d7bd836fc3cc4c6f0c9a7e/data/torl_data 。
保存原始文件、下载版本和 SHA-256，再生成独立的 benchmark；不覆盖共享数据。

该版本 train 有 28,740 行、28,738 道规范化去重题。先排除全部 test 身份，再从剩余
训练题中确定性抽出 256 道 dev，其余作为训练池。test 原样保留 374 行的采样次数：
MATH500 子集 134 道各一次、AIME24/25 各 30 道各四次，总计 194 道独立题目。
数据记录按问题保存重复次数，执行时展开成唯一 case ID，并保留共同 question ID。
最终报告逐行准确率与按独立题目聚类的 paired bootstrap 区间；重复采样不作为新增独立题。
聚类时不报告假定逐行独立的 exact McNemar p。该 MATH500 子集分数需标明 134 题规模。

任何生成内容都不包含 held-out 题的改写。worker 只传用户题面给模型，answer 只传给 reward；
数据中的 system 消息由固定的后端 TIR system prompt 替换。ToRL 无完整参考推导，不能用
`real_min_steps` 过滤它的难度，初始值设为0。初始 recipe 使用官方训练题，再由 Leader 根据
训练/dev 证据研发合成、筛选和课程。训练数据约15.6%的原始答案不是整数，不能假设整数
文字题合成器能直接覆盖竞赛数学。

本次固定 harness：Python/calculator、max_turns=2、max_length=3000、tool_timeout=30，
基模型为 Qwen2.5-Math-1.5B。评测 seed=42、每段最多512个新 token、temperature=0.6，
每个官方评测行执行一次，所以 AIME 每题有四次采样。训练每题的 n_samples 单独配置。
这些设置在新 study prepare 时冻结；所有模型候选使用同一预算，不迭代 harness。

GSM8K 仍可通过 `benchmark --format gsm8k` 单独准备，用于初步研究；其历史实验与 ToRL
研究分开保留，不作为原生 TIR 评测的替代。

合成代码 `synthesis.py` 提供库存、速率、预算、比例和递推五类题，约束数值范围和语义，
用独立计算交叉验证答案；混合真实训练题，确定性抽样、去重、限制重试次数。
DataRecipe 控制真实/合成数量、真实题最少推理步数、题型权重、合成难度和随机种子。
不把目前这份初始策略当作最终研究结果：Leader 需要根据 dev 失败模式修改/验证它。

当前研究策略还提供 `percent`、`average`、`units`、`work` 和 `decimal` 五类合成题。
它们只生成非负整数目标：百分比、单位和平均值使用 `Fraction` 检查精确整除，工作收入使用
逐项累加 oracle，decimal 使用十分位整数与 Fraction oracle；平均值先固定目标均值再反推最后
一个正数。所有生成题的 identity 由规范化题面计算，数据集确定性抽样、去重并只允许来自
`benchmark.train` 的真实题。复现实验时使用 `DataRecipe` 的 seed、计数、family weight 和
难度范围，并在提交 GPU 前运行 `pytest tests/test_tir_study.py`、`uv run --locked mypy`
和生成器的多 seed 边界验证。

prepare 冻结数据哈希、harness、reward、worker、数据读入和后端代码、基模型、推理预算。
源码快照包含未提交文件和完整归档；每个 GPU 阶段前后校验源码，运行阶段不能同时改代码。
数据策略与训练 recipe 可在阶段之间迭代，每轮保存假设、配方、源码、生成数据与父模型。
模型 warm start，但每轮优化器重新初始化。训练检查所需步数，最后一步才保存 checkpoint。
阶段使用文件锁保证单个写入者；失败记录保留，成功的评测不重复提交；训练失败用新轮次名，
训练已完成但 dev 失败时可单独重试 evaluate，使用独立 attempt 名。

至少完成两轮真实 Leader 训练，通过 dev 选全 study 最好的候选，selection 一经保存不再调参。
最终阶段独立运行基模型和选定模型，对齐逐题/逐次结果，报告净增益、wins/losses、固定种子
按题目聚类的 paired bootstrap 95% 区间。目标 +3pp，是否显著另行报告，不能把区间含零说成
显著提升。194道独立题目的区间可能较宽；目标没有达成时如实报告，不能用 final 继续调参。
本例的 held-out 限制依赖研究规范和哈希校验，不宣称同一 Unix 用户下有恶意访问隔离。

### 旧整数示例的数值诊断

整数 reward 会拒绝 `\boxed{9.00}` 等数学等价的小数。`numeric_audit.py` 是独立的
二级诊断，保留原始结果，使用最后一个完整 boxed 中的精确数值比较，支持有限小数、
整数分数、千位逗号和文字单位；不执行模型表达式，不从解释文字中猜答案。
审计保存评分源码、数据与原评测的内容引用，生成独立样本用于逐题配对比较。
GSM8K 研究应同时分析两个指标，才能区分格式修复与数值推理收益。ToRL 使用原生数学
等价评分，不能用这个只支持数值答案的诊断替代其包含符号表达式的评分。

```bash
python -m examples.tir.numeric_audit --evaluation /absolute/study/dev/base.json \
  --dataset /absolute/benchmark/dev.jsonl --output /absolute/study/numeric-dev/base
```

宿主 smoke/bootstrap 只验证 pipeline，不能当作由 RSI Leader 执行的轮次。Leader 至少
研究和训练两轮，保存研发依据，且选择时需与全 study 的最佳已完成候选比较。

## 运行

以下命令从项目根目录执行。准备 parquet 的可选依赖 pyarrow 位于 AReaL 镜像中，
不需要装进 RSI 宿主环境。CPU 数据准备也通过 Slurm 执行。

```bash
python -m examples.tir.benchmark --format torl --source /absolute/torl_data --output /absolute/benchmark
python -m examples.tir.study --study /absolute/study prepare --config examples/tir/study.json
python -m examples.tir.study --study /absolute/study evaluate --candidate base
python -m examples.tir.rsi --study /absolute/study --env-file /absolute/private.env
```

study.json 中的 benchmark 路径指向第一步产物。study 自动申请 rsi reservation 的 2 GPU、
16 CPU、128G，阶段结束即释放，不会在 Leader 分析时占住 GPU。
最后一个命令使用真实 ResearchSystem 和模型 API：Interaction 整理目标并交接 Leader，
Leader 修改合成代码/数据配方、多轮训练与分析，然后提交 decision.json。该命令完成 dev
研发后返回；宿主核对证据后独立执行 `study --study /absolute/study finalize --candidate NAME`，
冻结选择并运行 final。会话、角色迁移、工具和原生历史保存在 study/rsi，凭据由环境读取。
原生 Codex CLI 需要 >=0.162.1。已有中断会话使用同一 study 加 `--resume`，不要另建会话。

无需启动 RSI 也可手动执行 `study round --name r1 --recipe examples/tir/recipes/r1.json`。
这用于开发阶段检查，不能作为“真实 RSI 驱动研究”的证据。
