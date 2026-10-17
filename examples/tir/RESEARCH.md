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

合成代码 `synthesis.py` 当前提供 15 个题族：库存、速率、预算、比例、递推、百分比、
平均值、单位、工作收入、小数收入，以及分数、根式、线性约束、组合计数和等差数列。
每个题族保留题面参数的精确 oracle 和独立验算；混合真实训练题时确定性抽样、去重并限制
重试次数。新构建数据的 `DataRecipe.gold_format` 可以把 bare 数学答案放入 `$...$` 分隔符，
以适配原生 MathVerify；它不修改问题、数学目标、官方训练文件或已经保存的数据。旧 recipe
缺少该字段时按 `preserve` 解码，保持历史记录兼容。

`DataRecipe` 控制真实/合成数量、真实题最少推理步数、题型 proposal weight、合成难度、
难度权重、答案表示和随机种子。难度目前主要改变生成参数的数值范围，并不是已经校准的
推理难度；题型权重只是提议分布，去重和有限重试会让最终数量偏离权重。组合计数族当前
支持范围较小（r2 中只有 23 道不同 committee 题），不能用人名或表面改写冒充数学覆盖。
不把目前这份初始策略当作最终研究结果：Leader 需要根据 dev 失败模式修改/验证它。

旧的五类整数题仍使用 `Fraction`、逐项累加或十分位 Fraction oracle；新增五类保留非整数
分数和根式目标，线性约束用代入验算，组合题用枚举，数列用逐项递推。所有生成题的 identity
由规范化题面计算，数据集确定性抽样、去重并只允许来自 `benchmark.train` 的真实题。复现实验
时使用 `DataRecipe` 的 seed、计数、family weight、difficulty weights 和范围，并在提交 GPU
前运行 `pytest tests/test_tir_study.py`、`uv run --locked mypy` 和生成器的多 seed 语义验证。

prepare 冻结数据哈希、harness、reward、worker、数据读入和后端代码、基模型、推理预算。
源码快照包含未提交文件和完整归档；每个 GPU 阶段前后校验源码，运行阶段不能同时改代码。
数据策略与训练 recipe 可在阶段之间迭代，每轮保存假设、配方、源码、生成数据与父模型。
模型 warm start，但每轮优化器重新初始化。训练检查所需步数，最后一步才保存 checkpoint。
阶段使用文件锁保证单个写入者；失败记录保留，成功的评测不重复提交；训练失败用新轮次名，
训练已完成但 dev 失败时可单独重试 evaluate，使用独立 attempt 名。
取消或失败也必须保留 recipe、生成数据、源码快照、job/allocation/stdout；取消清理针对
该作业的进程树，不能删除实验记录。恢复记录需标明原始哈希可验证的部分和未恢复的部分。

至少完成两轮真实 Leader 训练，通过 dev 选全 study 最好的候选，selection 一经保存不再调参。
最终阶段独立运行基模型和选定模型，对齐逐题/逐次结果，报告净增益、wins/losses、固定种子
按题目聚类的 paired bootstrap 95% 区间。目标 +3pp，是否显著另行报告，不能把区间含零说成
显著提升。194道独立题目的区间可能较宽；目标没有达成时如实报告，不能用 final 继续调参。
本例的 held-out 限制依赖研究规范和哈希校验，不宣称同一 Unix 用户下有恶意访问隔离。

### 从真实 rollout 构建课程

`select_mixed_rollout_curriculum` 读取实际训练 logger 的 JSONL，选择同一个生成组中
同时观察到 reward 0 和 1 的官方训练题。它逐行解码具名记录，验证原训练数据、冻结训练池、
题面、样本序号与二值奖励，并将日志及提取代码的内容引用写入选择结果。已知合成题的日志
经过验证后跳过；未知题、held-out、伪造题面或重复样本直接失败。

完整组与部分日志分别计数；部分组只要已观察到 0/1 也可入选，不把不同文件或不同 epoch
拼成一个组。空日志、缺失样本不能当作全对、全错或被优化器拒绝的证据。AReaL logger
会跳过某些样本，所以日志奖励均值不是完整训练奖励的无偏估计。

下面复现本次 r3→r4 的课程提取；使用新的输出文件名，保留旧记录。选择完成后通过
`DataRecipe.real_task_ids` 保存确切 ID，数据构建时再解析到冻结训练池，而非另读任意数据集。

```python
from pathlib import Path
from areal_rsi.infra.artifacts import read_record, write_record
from examples.tir.benchmark import read_examples
from examples.tir.pipeline import local_path
from examples.tir.study import Candidate, protocol
from examples.tir.synthesis import select_mixed_rollout_curriculum

root = Path("/absolute/study")
frozen = protocol(root)
observed = read_record(root / "rounds/r3/candidate.json", Candidate)
logs = root / "execution" / root.name / "jobs/r3-train/areal/logs"
selection = select_mixed_rollout_curriculum(
    read_examples(local_path(frozen.benchmark.train)),
    read_examples(local_path(observed.data.dataset)),
    tuple(sorted(logs.glob("*/rsi-tir/*/rollout/*/*.jsonl"))),
    frozen_pool_source=frozen.benchmark.train,
    trained_dataset_source=observed.data.dataset,
    n_samples=4,
    excluded_ids=frozen.benchmark.excluded_ids,
)
write_record(root / "data-audits/new-curriculum-selection.json", selection)
```

本次得到 263 道题：192 个完整 mixed 组、71 个部分 mixed 组。`recipes/r2.json`、
`r3.json`、`r4.json` 保存这次试验的配方；r4 的确切题目 ID 依赖本次训练池与 r3 日志，
其他 benchmark 应重新提取。它从 r1 checkpoint 训练，课程证据来自 r3；这两种关系应分别
记录。这是一项实证课程策略，不保证每种模型或训练阶段都获益，也不能从这次同时变化的
数据、学习率和训练步数中分离单因果。

本次四轮真实 RSI 及独立最终评测的结果、复现产物入口见 [RESULTS.md](RESULTS.md)。
新 study 的 `torl-r1.json` 显式使用 `math_delimited`；本次历史 r1 使用 `preserve`，逐字复现
应读取 study 内 `rounds/r1/recipe.json`，而非后续更新的入门模板。

### 旧整数示例的数值诊断

整数 reward 会拒绝 `\boxed{9.00}` 等数学等价的小数。`numeric_audit.py` 是独立的
二级诊断，保留原始结果，使用最后一个完整 boxed 中的精确数值比较，支持有限小数、
整数分数、千位逗号和文字单位；不执行模型表达式，不从解释文字中猜答案。
审计保存评分源码、数据与原评测的内容引用，生成独立样本用于逐题配对比较。
GSM8K 研究应同时分析两个指标，才能区分格式修复与数值推理收益。ToRL 使用原生数学
等价评分，不能用这个只支持数值答案的诊断替代其包含符号表达式的评分。

本次发现所用 AReaL MathVerify 对某些 bare LaTeX gold 的解析有误，因此在最终评测前
另行冻结了“最后一个完整 boxed 答案 + 数学分隔 gold”的二级诊断，复用同一批生成轨迹。
它要求显式完整 boxed，与原生主评分不互为无损变换；两个指标都需保留。原生评分器、
官方评测数据和主结果没有在研究中修补。相关冻结脚本、运行环境与执行限制见结果报告。

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

无需启动 RSI 也可手动执行 `study round --name r1 --recipe examples/tir/recipes/torl-r1.json`。
这用于开发阶段检查，不能作为“真实 RSI 驱动研究”的证据。
