# Qwen3-8B 搜索训练与自动实验

本例把固定的 BrowseComp-Plus Qwen-Agent ReAct 行为接入 AReaL v1 在线 GRPO，使用外部搜索问答训练，并由原生 `SessionSystem` Goal 选择后续实验。主目标和15% 的验收门槛见[示例研究目标](OBJECTIVE.md)。一次候选 pipeline 完成不等于整个研究 Goal 完成。

## 标准研究入口

Search 通过 problem.py:SearchProblem 接入统一 SessionSystem。任务保留检索、reward、
rollout、训练 worker 和官方评测；候选、Slurm 回执、恢复与选择冻结由 infra 管理。
experiment/pipeline.py 只执行一个 GPU 阶段，在 allocation 内启动并清理检索服务与 worker。

控制节点需要已有 RSI 依赖、Codex、可达的模型 API 和 Slurm 客户端。GPU 依赖保留在各自
容器环境；不要在运行期间重建控制器的共享 Python 环境。GPU 实验 ID 与控制器自身的 Slurm
job ID 分开记录，不能把控制器当作训练实验等待或取消。

配置格式是 SearchProblemConfig，完整模板为 research.json：search 为 SearchConfig，另填 image、slurm 和最低轮次。
search.project 指向包含新接入代码的冻结源码；worker 使用其中的绝对入口及工作目录，避免
调用目录中的同名模块覆盖。数据/索引、embedding、检索 Python、GPU Python 与镜像需要预先可用。
后端源码根目录需是有 HEAD 的 Git 工作树，供 infra 生成源码快照。若历史 runtime 只有源码副本，
先复制到新的实验目录、逐文件校验一致后建立独立仓库，不修改历史目录。
search.model 中的文件须实际位于模型目录内。历史 YaRN 模型若通过符号链接引用目录外权重，
应在新实验目录展开复制并校验所有文件哈希，保持原配置与权重内容一致。

从仓库根目录运行，并在启动和恢复会话的 shell 中设置示例导入路径：

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
areal-rsi research --problem examples.search.problem:SearchProblem \
  --config /absolute/search.json --objective /absolute/objective.md \
  --root /absolute/runs/search-001 --env-file /absolute/private.env
```

目标正文保存本轮完整研究要求。配置由 operator 指定并冻结，不再通过 prompt 猜测可执行
配置或提交命令。SearchRecipe 包含 training 参数，以及可选的 dataset_directory、index_directory，
用于给候选指定独立版本的训练题、语料和索引；这些输入逐项记录内容哈希。训练题的 ID 和来源分组
不得与冻结的 dev/test 重合。候选数据路径只影响训练，dev/test、评测检索资源和 harness 始终使用
原研究配置。Leader 用统一工具提交候选和读取 dev 证据。

training.microbatch 分别指定 max_tokens_per_mb 和 n_mbs，默认 131072/8；
这属于训练打包参数，不修改固定的推理上下文或预算。

```bash
areal-rsi status --root /absolute/runs/search-001 --resume run-ID
areal-rsi research --root /absolute/runs/search-001 --resume run-ID --env-file /absolute/private.env
areal-rsi finalize --root /absolute/runs/search-001 --resume run-ID --candidate candidate-001
```

finalize 默认独立评测外部 test。配置 official（FinalEvalConfig）和 official_image 后，使用
冻结 H0、原始官方 judge 和完整 830 题执行最终验收，同时保存原始官方产物。配置中的主指标
增益检查只覆盖一个验收条件；其他种子、成本和泛化等目标仍需按 objective 的原始要求验证。
接口、配置、目录和恢复说明见 [统一接入文档](../../docs/interfaces/README.md)。

## 数据与奖励

`task/dataset.py` 从固定版本外部 HotpotQA 构建问题、答案、证据及配套语料，按来源标题/文档分组拆分，过滤与基准问题近似重复的内容。`task/retrieval.py` 使用固定 Qwen3-Embedding-8B 建索引，验证答案确实存在于512-token工具片段。外部 test 用于最终泛化，候选选择使用 dev。训练、SFT 和合成数据提示词不得包含 BrowseComp-Plus830题、答案或历史轨迹。

答案出现在证据中不足以证明问题前提成立。G4 试跑已发现矛盾前提、实体粒度错误及严格字符串奖励的误判；数据 QC、显式且有证据的别名、语义评分需要单独记录版本和证据。不要用任意答案子串命中替代正确性，也不要把服务故障当成普通零奖励。

训练接口只返回 terminal float，由 AReaL 回传奖励并保留逻辑 rollout 分组。logprob、loss mask 和权重版本由 AReaL 的标准训练流程管理；本例不增加后端审计回调，也不重复保存训练张量。同题训练采样不固定相同 seed，评测固定 seed。初始设置为 B8/G4、lr=1e-6、group reward normalization、KL=0，后续由开发集和训练奖励分布决定。

`dump_to_file` 已启用，原生轨迹位于作业输出的 `areal/logs/<user>/rsi-search/<trial>/rollout/` 或 `eval-rollout/` 下。每个 JSONL 行记录模型调用的 prompt/completion、长度、奖励、策略版本和任务信息。评测按 task ID 将这些文件与 `episodes/` 中的问答结果关联，使用 individual export 的 `prompt_len` 和 `seqlen` 累计输入输出 token；整个任务缺失、记录重复或任务 ID 不匹配时评测失败；单个任务内部的部分行缺失仍待补充检查。原生 dump 不包含完整逐 token logprob 向量，本例不把它作为 RSI 验收项。

## 产物、恢复与验收

`evaluation/publish.py` 在指定的冻结 runtime 内定位 `examples/search/evaluation/official.py`；历史冻结 H0 则使用原来的 `examples/search/final_evaluation.py`。两种布局均直接调用该 runtime 中的绝对路径，不改 source-lock。新实验需冻结新的源码，历史配置、数据清单和已有产物按原路径保留。

官方 H0 可在已计费的三 GPU Slurm 作业内由 `python -m examples.search.evaluation.publish --config <冻结配置> --runtime <冻结H0源码> --image <固定镜像>` 执行。外层只把该配置的输出目录绑定到节点 `/tmp`，模型、索引、源码和所有推理及评分参数仍使用原冻结值；不改动 H0 文件。已完成的 solver JSON 周期复制回共享目录；共享写入异常会延后复制，不打断本地推理。只有冻结 H0 子进程成功结束、全部产物逐字节校验后，才最后发布 `result.json`。配置、完整本地产物和状态保留在 `/tmp/search-rsi-official-<jobid>/`。若推理已完成而复制失败，可在同一节点的 CPU 作业中运行 `python -m examples.search.evaluation.publish --publish-only <本地state.json>`，不重新生成答案。本地 scratch 不防节点丢失，正式产物以校验后的共享副本为准。

若整轮推理和未修改的官方 judge 已完成，旧包装器却因为上下文预算耗尽而拒绝汇总，可在源作业确认终态后执行 `python -m examples.search.evaluation.official --recover-summary <原config.json> --output <原目录之外的新summary.json>`。这是单独版本化的 CPU 汇总恢复：只接受精确的 HTTP400 输入加预留输出超过 131072、输出预算仍为10000，且原始 Qwen 客户端记录为 incomplete、没有最终答案、官方判错、原始与规范轨迹一致的情况。它保留全部830题与官方评分，不重生成、不截断输入、不改原始文件；其他服务错误、超时、缺失原始证据或 judge 解析失败仍拒绝。恢复 summary 的 sidecar receipt 保存输入文件哈希、原作业终态及预算耗尽记录，费用使用原 solver 计数和实际 Slurm allocation 耗时。使用恢复 summary 时，最终配对报告必须明确引用该文件和 receipt，不能把它写回旧 `result.json` 或声称旧包装器成功退出。

`--verify-runs <原config.json> --output <新receipt.json>` 独立检查完整830份已生成结果的 query/seed/协议哈希、检索 docid 并集、原始记录和已知预算耗尽。仅对能从原始参数与客户端堆栈证明发生在派发前的非法模型参数（损坏JSON、JSON字符串而非对象）接受 Qwen-Agent 的文本错误观察，模型后续恢复与最终答案原样保留；未知错误、服务故障和不一致证据仍拒绝。原始文件只读，校验不调用模型。新版本主入口在批处理返回非零后先保存真实服务计数，再做此校验并决定是否进入未修改的官方 judge，避免末尾校验错误丢失计数；历史冻结入口继续保留旧行为，须独立记录仅评分续跑。若历史终态计数确已丢失，记录已观测下界及缺口，禁止补造精确数值或宣称已通过全部最终验收。

研究开始时评测 baseline，每个候选真实训练后由独立推理进程重载 checkpoint 并评测 dev。配置、请求、原生轨迹、训练日志、checkpoint manifest、参数变化和选择记录统一保存在 `sessions/<run_id>/problem/`。同一执行身份不重复提交 allocation；已有结果需通过内容哈希校验。训练失败保留证据并使用新候选 ID；已完成训练但 valid 中断时可显式重试该评测 attempt。

提交通过 [`sbatch --hold`](https://slurm.schedmd.com/sbatch.html) 保证作业在初始回执保存前不会开始执行。初始回执写入失败时取消该作业；取消未确认时，报错保留 job ID 供核查。放行状态尚未确认的回执记录 `release_pending`，重试同一 run ID 会核查并恢复原作业；放行响应丢失或后续回执写入失败也不会重复提交。若提交响应丢失且没有回执，保留的提交目录会阻止自动重提，应先按 run ID 对应的 Slurm 作业名核查 held 作业。

候选在相同 dev、harness、种子和推理预算下比较；最终冻结已完成候选中的最佳改进结果。最终候选须在完整830题评测前冻结；原 H0、相同推理预算、官方索引和官方评分的 Accuracy 至少125/830，并补充其他种子、配对统计、Evidence Recall、搜索次数、真实 token、延迟和全部 GPU 成本。修改 harness 时另做归因对照。

冻结最佳模型后，默认 finalize 使用相同数据/索引配置独立评测外部 test，并分别重载基线和选定模型。该模式申请2张 GPU，记录 test split，保留冻结选择。配置 official 后，finalize 改为完整官方评测；其他独立泛化与补充种子仍按原研究目标另行记录。

```bash
areal-rsi finalize --root /absolute/runs/search-001 --resume run-ID --candidate candidate-001
```

全量评测使用 `evaluation/artifacts.py` 中的 `FinalEvalConfig` JSON，分别指定模型、原始 tokenizer/generation-config、模型文件 manifest 及其 SHA256、`asset_lock` 及其 SHA256、原检索环境 Python 和 vLLM Python。asset lock 引用并校验原模型、embedding、judge、官方索引、语料、题目/标签、snippet tokenizer、Qwen-Agent 和冻结评测源码的文件清单。题目和标签仅用于全量评测，不能成为训练输入。官方索引文件另按公开 LFS SHA256 校验来源。提交脚本传入 host 核验的 Slurm allocation，使用3张 GPU；若配置 reservation，还会核对其名称。

训练镜像使用 HF5，最终 vLLM 镜像使用 Transformers4.57。HF5 将 `rope_theta` 移入 `rope_parameters`；直接使用该 checkpoint 时，旧环境会采用错误的默认 theta。先在项目环境中生成兼容导出，补齐对应的 legacy RoPE/dtype 字段，并检查每个权重文件的 SHA256 未变。原始 checkpoint 保留，导出 manifest 与来源记录位于新目录旁。最终配置的 `model` 和 `checkpoint_manifest` 指向导出目录及其 manifest；评测仍使用原冻结 H0 运行时和 asset lock。

```bash
python -m examples.search.evaluation.official \
  --export-checkpoint /path/to/original/checkpoint.json \
  --source-sha256 ORIGINAL_CHECKPOINT_MANIFEST_SHA256 \
  --output /path/to/new/inference-export
```

```bash
python -m examples.search.evaluation.official --config /path/to/frozen-final-config.json
python -m examples.search.evaluation.analysis \
  --baseline /path/to/baseline/official-results \
  --candidate /path/to/candidate/official-results \
  --queries /path/to/official/queries.tsv --output /path/to/paired-report.json
```

基线和候选沿用同一份 asset lock，候选 checkpoint 使用独立冻结的模型 manifest。主种子42，补充种子43；每次使用新的 output 目录。评测不会覆盖已有目录，正式结果必须覆盖全部830题，并通过 judge 解析与运行故障检查。

`make check` 检查 CPU 架构、测试、Ruff、Mypy 与 UI；原生轨迹读取和任务关联在 CPU 测试中验证。小文件或 mock 测试不提供模型学习的证据；真实 step、权重变化、完整保存、独立重载与评测必须由 GPU 产物证明。

## 新集群配置

模板中的 `/absolute/` 路径均需替换为实际资源路径。
先在训练镜像中安装所选 AReaL 版本及示例依赖，准备本地模型和评测数据，记录实际使用的
源码版本与镜像摘要。GPU Python 使用容器内路径，控制节点只需安装本仓库的轻量依赖。

顶层 `image` 必须显式提供。`binds` 是 Apptainer 挂载字符串列表，例如
`["/shared:/shared"]`；应覆盖 RSI/AReaL 源码、模型、数据、配置和输出目录，并保持相同
绝对路径。空列表只使用 Apptainer 自带挂载，不再默认挂载 `/storage`。
`slurm.reservation` 仅在实际持有时填写，`null` 表示不指定 reservation。
分区使用集群默认调度策略。
