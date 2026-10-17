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

本例统一使用 `train / valid / test`：train 用于训练，valid 用于多轮研发和候选选择，
test 仅供冻结选择后的独立验收。**test 对应 ToRL 官方 test**，保留全部题目和采样次数，
在公共评测契约中仍使用 `BenchmarkSplit.TEST`；它不参与调参。新 benchmark 保存
`heldout/test.jsonl`，结果保存 `study/test/`，验收入口为 `study test`。

该版本 train 有 28,740 行、28,738 道规范化去重题。先排除全部 test 身份，再从剩余
训练题中确定性抽出 256 道 valid，其余作为训练池。test 原样保留 374 行的采样次数：
MATH500 子集 134 道各一次、AIME24/25 各 30 道各四次，总计 194 道独立题目。
数据记录按问题保存重复次数，执行时展开成唯一 case ID，并保留共同 question ID。
最终报告逐行准确率与按独立题目聚类的 paired bootstrap 区间；重复采样不作为新增独立题。
聚类时不报告假定逐行独立的 exact McNemar p。该 MATH500 子集分数需标明 134 题规模。

任何生成内容都不包含 held-out 题的改写。worker 只传用户题面给模型，answer 只传给 reward；
数据中的 system 消息由固定的后端 TIR system prompt 替换。ToRL 无完整参考推导，不能用
参考解中的步骤标记判断难度。初始 recipe 使用官方训练题，再由 Leader 根据
训练/valid 证据研发合成、筛选和课程。训练数据约15.6%的原始答案不是整数，不能假设整数
文字题合成器能直接覆盖竞赛数学。

本次固定 harness：Python/calculator、max_turns=2、max_length=3000、tool_timeout=30，
基模型为 Qwen2.5-Math-1.5B。评测 seed=42、每段最多512个新 token、temperature=0.6，
每个官方评测行执行一次，所以 AIME 每题有四次采样。训练每题的 n_samples 单独配置。
这些设置在新 study prepare 时冻结；所有模型候选使用同一预算，不迭代 harness。

合成代码 `synthesis.py` 当前提供 15 个题族：库存、速率、预算、比例、递推、百分比、
平均值、单位、工作收入、小数收入，以及分数、根式、线性约束、组合计数和等差数列。
每个题族保留题面参数的精确 oracle 和独立验算；混合真实训练题时确定性抽样、去重并限制
重试次数。新构建数据的 `DataRecipe.gold_format` 可以把 bare 数学答案放入 `$...$` 分隔符，
以适配原生 MathVerify；它不修改问题、数学目标、官方训练文件或已经保存的数据。
该字段默认 `preserve`，首轮模板显式选择 `math_delimited`。

`DataRecipe` 控制真实/合成数量、确切训练题 ID、题型 proposal weight、合成难度、
难度权重、答案表示和随机种子。难度目前主要改变生成参数的数值范围，并不是已经校准的
推理难度；题型权重只是提议分布，去重和有限重试会让最终数量偏离权重。组合计数族当前
支持范围较小（r2 中只有 23 道不同 committee 题），不能用人名或表面改写冒充数学覆盖。
不把目前这份初始策略当作最终研究结果：Leader 需要根据 valid 失败模式修改/验证它。

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
训练已完成但 valid 失败时可单独重试 evaluate，使用独立 attempt 名。
取消或失败也必须保留 recipe、生成数据、源码快照、job/allocation/stdout；取消清理针对
该作业的进程树，不能删除实验记录。恢复记录需标明原始哈希可验证的部分和未恢复的部分。

至少完成两轮真实 Leader 训练，通过 valid 选全 study 最好的候选，selection 一经保存不再调参。
最终阶段独立运行基模型和选定模型，对齐逐题/逐次结果，报告净增益、wins/losses、固定种子
按题目聚类的 paired bootstrap 95% 区间。目标 +3pp，是否显著另行报告，不能把区间含零说成
显著提升。194道独立题目的区间可能较宽；目标没有达成时如实报告，不能用 test 继续调参。
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
recipe 统一为 `recipes/r1.json` 到 `r4.json`，全部面向 ToRL；文件名表示实验轮次，
权重父候选由 `parent` 指定。新 study 的 `r1.json` 显式使用 `math_delimited`；本次历史 r1
使用 `preserve`。历史实验应使用归档源码、协议和 `rounds/r1/recipe.json` 复现，不能用
更新后的模板代替。命名与配置清理只影响新实验，不迁移或重写已有冻结协议和数据。

### 数学答案诊断

本次发现所用 AReaL MathVerify 对某些 bare LaTeX gold 的解析有误，因此在最终评测前
另行冻结了“最后一个完整 boxed 答案 + 数学分隔 gold”的二级诊断，复用同一批生成轨迹。
它要求显式完整 boxed，与原生主评分不互为无损变换；两个指标都需保留。原生评分器、
官方评测数据和主结果没有在研究中修补。相关冻结脚本、运行环境与执行限制见结果报告。

宿主 smoke/bootstrap 只验证 pipeline，不能当作由 RSI Leader 执行的轮次。Leader 至少
研究和训练两轮，保存研发依据，且选择时需与全 study 的最佳已完成候选比较。

## 合成数据复盘与下一步

本次只观察到两套合成配方没有稳定提升 valid，不能据此认定合成数据无效。r2 相对其父模型
r1 从 78/256 降到 69/256；r3 主评分为79/256，但 boxed 数学诊断从80降到78。r4 未使用
合成题，不能用它的 test 收益证明合成策略有效。所有轮次只有一个训练种子，且配比、
学习率、更新次数和生成长度共同变化，缺少控制变量的消融。

实际日志的完整组（同题4次生成均有记录）显示：

| 轮次 / 数据 | 完整组 | 全对 | 全错 | 有对有错 | 有对有错占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| r2 合成 | 356 | 271 | 72 | 13 | 3.65% |
| r2 官方 | 342 | 38 | 180 | 124 | 36.26% |
| r3 合成 | 120 | 109 | 0 | 11 | 9.17% |
| r3 官方 | 361 | 35 | 134 | 192 | 53.19% |

数据来自历史 study 的 `training-coverage-native-v2/r2.json`、`r3.json`。logger 会跳过
部分样本，因此这些比例只描述完整日志组，不是完整训练分布或有效梯度的无偏估计。
本次 AReaL 配置按题目组归一化奖励；同组全对或全错时，二值正确性奖励的相对信号为零。
合成题占用了20%或40%的题目预算，记录到的可区分组却很少，是目前最直接的问题证据。
[DAPO 的动态采样](https://arxiv.org/html/2503.14476v1#S3.SS2)也针对这类全同奖励组。

还有两项代码层面的限制。第一，difficulty 只改变数值范围，分数族仍是带分数转换，
linear 仍是一步方程，counting 仍是基础选人组合；没有覆盖足够的条件建模、约束组合、
几何推理和枚举策略。第二，题面去重不等于数学结构多样性，r2 的 counting 仅有23道题。
精确 oracle 能保证答案正确，不能保证题目适合当前模型，也不能保证向竞赛数学迁移。
独立审计已覆盖本次全部合成题，现有证据不支持把退化主要归因于合成答案错误。

训练剂量也需要控制：r2 的64步、batch16只消费1,024个题目组，r3 的48步只消费768组，
两轮数据集各有1,280题。生成题目数不等于实际学习量；r4则在263道聚焦真实题上消费
1,536个题目组。数据筛选与重复训练剂量需要分别比较。

下一轮优先考虑以下研发，全部保持训练题来自官方 train 或独立生成，harness 固定：

1. **动态课程。** 在官方训练池上用当前学生模型采样，优先选择当前有对有错的题，
   定期刷新而不是始终复用r3筛出的263题；保留随机真实题以维持覆盖。按题型报告学习信号、
   截断、工具错误、完整答案率和采样成本，避免只追求训练奖励。
2. **改造合成准入。** 生成多条件、可组合、需要真实工具交互的任务；先经精确求解与
   独立验算，再用学生 rollout 校准难度，按数学结构去重。全对题降采样，全错题检查奖励
   解析和预算后降难度或进入示范学习；不只增加数字大小或表面改写。
3. **正确工具轨迹的 SFT / 蒸馏，再做 RL。** 对学生始终全错的训练题，由教师模型生成
   可执行的解题轨迹，核验工具执行和答案后做少量 SFT，再用 GRPO 练习探索与纠错。
   这需要新增示范训练路径，当前 pipeline 尚未实现；不能把“只合成题面与答案”称为
   轨迹蒸馏。参考 [STaR](https://arxiv.org/abs/2203.14465) 的生成、筛选、训练循环及
   [DeepSeek-R1](https://arxiv.org/abs/2501.12948) 的冷启动与蒸馏设计。
4. **奖励与训练预算。** 新 study 先校准分数、根式、等价表达式和未完成回答的奖励；
   排查截断后再单独比较训练生成长度、每题采样数和更新剂量。先固定评测预算，只改变
   训练设置；更长训练生成并不自动改善固定预算下的回答完成率。本次r2缺少完整boxed的
   dev回答为66条，r1为31条，但多变量变化不能证明由生成长度单独造成。

建议先从同一父checkpoint做“均匀官方题 / 动态真实题课程 / 课程加少量准入合成题”三组
对照，固定优化器配置、更新次数、生成预算和 harness，并同时记录实际token及GPU成本。
先依据 valid 比较，再扩展到多训练种子。上述方法是待验证的研发方向，没有在本次额外
执行训练。已使用的 test 不再用于调参；改目录或改随机种子不会让它重新成为未见数据。

## 运行

以下命令从项目根目录执行。准备 parquet 的可选依赖 pyarrow 位于 AReaL 镜像中，
不需要装进 RSI 宿主环境。CPU 数据准备也通过 Slurm 执行。

```bash
python -m examples.tir.benchmark --source /absolute/torl_data --output /absolute/benchmark
python -m examples.tir.study --study /absolute/study prepare --config examples/tir/study.json
python -m examples.tir.study --study /absolute/study evaluate --candidate base
python -m examples.tir.rsi --study /absolute/study --env-file /absolute/private.env
```

study.json 中的 benchmark 路径指向第一步产物。study 自动申请 rsi reservation 的 2 GPU、
16 CPU、128G，阶段结束即释放，不会在 Leader 分析时占住 GPU。
最后一个命令使用真实 ResearchSystem 和模型 API：Interaction 整理目标并交接 Leader，
Leader 修改合成代码/数据配方、多轮训练与分析，然后提交 decision.json。该命令完成 valid
研发后返回；宿主核对证据后独立执行 `study --study /absolute/study test --candidate NAME`，
冻结选择并运行 test。会话、角色迁移、工具和原生历史保存在 study/rsi，凭据由环境读取。
原生 Codex CLI 需要 >=0.162.1。已有中断会话使用同一 study 加 `--resume`，不要另建会话。

无需启动 RSI 也可手动执行 `study round --name r1 --recipe examples/tir/recipes/r1.json`。
这用于开发阶段检查，不能作为“真实 RSI 驱动研究”的证据。
