# Qwen3-8B 搜索训练与自动实验

本例把固定的 BrowseComp-Plus Qwen-Agent ReAct 行为接入 AReaL v1 在线 GRPO，使用外部搜索问答训练，并由原生 `ResearchSystem` Goal 选择后续实验。主目标和15% 的验收门槛见项目根目录 `search-rsi.md`。一次候选 pipeline 完成不等于整个研究 Goal 完成。

## 入口与环境

- `goal.py`：创建或显式恢复原生 RSI 会话，保留 session、事件和进度。控制进程需要 Python3.12、现有 RSI 依赖、Codex>=0.162.1、可达的模型 API，以及 Slurm 客户端。
- `pipeline.py --submit`：以 hold 状态提交 `all/rsi` 候选作业，保存 Slurm 回执后放行，同一 run ID 不重复提交。
- `worker.py`：GPU 容器中的真实 AReaL train/eval；训练后检查实际保存步数和参数变化。
- `agent.py`：复用 Qwen-Agent0.0.27 的同步 ReAct 行为，经 AReaL HTTP 代理生成在线轨迹；记录答案、奖励、搜索次数和耗时等任务结果。
- `final_evaluation.py`：最终官方 H0 评测入口，使用原 benchmark runner、官方100195文档索引和未修改的32B judge，读取服务器计数器统计 token。
- `analysis.py`：完整覆盖检查、配对 bootstrap 区间、精确 McNemar 检验和 Wilson 区间。
- `rollouts.py`：按 AReaL task ID 关联任务结果与原生轨迹文件，汇总评测 token 用量。

RSI 控制器在 Slurm CPU host 环境中运行；训练、检索、独立推理在已验证的 GPU 容器中运行。容器未提供 Slurm 客户端，提交脚本在容器外读取 allocation 记录并传入，worker 核对 JobId 与 reservation。不要在控制器运行期间重建它的共享 Python 环境，也不要将自己的控制 Job 当成训练作业等待、取消或递归启动。

启动原生 Goal：

```bash
python -m examples.search.goal \
  --root /path/to/run/native-goal --workspace /path/to/rsi-worktree \
  --objective /path/to/objective.md --env-file /path/to/private/rsi.env
```

恢复同一会话时增加 `--resume`。私有 env 包含 `RSI_BASE_URL`、`RSI_MODEL`、`RSI_API_KEY`，不要复制进源码、冻结 runtime 或报告。API 代理必须从控制节点可达；提交机的127.0.0.1代理地址不能直接用于另一台计算节点。

提交一个已有冻结配置的真实候选：

```bash
python -m examples.search.pipeline --submit \
  --config /path/to/candidate.json --run-id candidate-001 \
  --image /path/to/areal-sglang.sif \
  --time-limit 14400
```

配置使用 `SearchConfig`，指向独立的 `project` 冻结源码、`runtime.repository` AReaL 源码、GPU Python、Qwen-Agent overlay、数据与索引目录。`dense_python` 指向带原 MCP/FAISS 工具依赖的检索环境。每个候选使用 actor+rollout+1检索 GPU，合计不超过8；同一输出根目录通过实验锁保护训练与 incumbent 更新，等当前训练候选结束后再提交下一个。规划器 key 不传到 GPU 作业。

提交入口直接执行 `project` 下的冻结脚本，检索子进程也在相同源码目录运行。仅设置 `PYTHONPATH` 再使用 `python -m`，可能被调用目录里的同名模块覆盖，不能作为源码冻结的保证。

## 数据与奖励

`data.py` 从固定版本外部 HotpotQA 构建问题、答案、证据及配套语料，按来源标题/文档分组拆分，过滤与基准问题近似重复的内容。`dense.py` 使用固定 Qwen3-Embedding-8B 建索引，验证答案确实存在于512-token工具片段。外部 test 用于最终泛化，候选选择使用 dev。训练、SFT 和合成数据提示词不得包含 BrowseComp-Plus830题、答案或历史轨迹。

答案出现在证据中不足以证明问题前提成立。G4 试跑已发现矛盾前提、实体粒度错误及严格字符串奖励的误判；数据 QC、显式且有证据的别名、语义评分需要单独记录版本和证据。不要用任意答案子串命中替代正确性，也不要把服务故障当成普通零奖励。

训练接口只返回 terminal float，由 AReaL 回传奖励并保留逻辑 rollout 分组。logprob、loss mask 和权重版本由 AReaL 的标准训练流程管理；本例不增加后端审计回调，也不重复保存训练张量。同题训练采样不固定相同 seed，评测固定 seed。初始设置为 B8/G4、lr=1e-6、group reward normalization、KL=0，后续由开发集和训练奖励分布决定。

`dump_to_file` 已启用，原生轨迹位于作业输出的 `areal/logs/<user>/rsi-search/<trial>/rollout/` 或 `eval-rollout/` 下。每个 JSONL 行记录模型调用的 prompt/completion、长度、奖励、策略版本和任务信息。评测按 task ID 将这些文件与 `episodes/` 中的问答结果关联，使用 individual export 的 `prompt_len` 和 `seqlen` 累计输入输出 token；缺失、重复或不匹配的记录会导致评测失败。原生 dump 不包含完整逐 token logprob 向量，本例不把它作为 RSI 验收项。

## 产物、恢复与验收

官方 H0 可在已计费的三 GPU Slurm 作业内由 `python -m examples.search.official_output --config <冻结配置> --runtime <冻结H0源码> --image <固定镜像>` 执行。外层只把该配置的输出目录绑定到节点 `/tmp`，模型、索引、源码和所有推理及评分参数仍使用原冻结值；不改动 H0 文件。已完成的 solver JSON 周期复制回共享目录；共享写入异常会延后复制，不打断本地推理。只有冻结 H0 子进程成功结束、全部产物逐字节校验后，才最后发布 `result.json`。配置、完整本地产物和状态保留在 `/tmp/search-rsi-official-<jobid>/`。若推理已完成而复制失败，可在同一节点的 CPU 作业中运行 `python -m examples.search.official_output --publish-only <本地state.json>`，不重新生成答案。本地 scratch 不防节点丢失，正式产物以校验后的共享副本为准。

若整轮推理和未修改的官方 judge 已完成，旧包装器却因为上下文预算耗尽而拒绝汇总，可在源作业确认终态后执行 `python -m examples.search.final_evaluation --recover-summary <原config.json> --output <原目录之外的新summary.json>`。这是单独版本化的 CPU 汇总恢复：只接受精确的 HTTP400 输入加预留输出超过 131072、输出预算仍为10000，且原始 Qwen 客户端记录为 incomplete、没有最终答案、官方判错、原始与规范轨迹一致的情况。它保留全部830题与官方评分，不重生成、不截断输入、不改原始文件；其他服务错误、超时、缺失原始证据或 judge 解析失败仍拒绝。恢复 summary 的 sidecar receipt 保存输入文件哈希、原作业终态及预算耗尽记录，费用使用原 solver 计数和实际 Slurm allocation 耗时。使用恢复 summary 时，最终配对报告必须明确引用该文件和 receipt，不能把它写回旧 `result.json` 或声称旧包装器成功退出。

`--verify-runs <原config.json> --output <新receipt.json>` 独立检查完整830份已生成结果的 query/seed/协议哈希、检索 docid 并集、原始记录和已知预算耗尽。仅对能从原始参数与客户端堆栈证明发生在派发前的非法模型参数（损坏JSON、JSON字符串而非对象）接受 Qwen-Agent 的文本错误观察，模型后续恢复与最终答案原样保留；未知错误、服务故障和不一致证据仍拒绝。原始文件只读，校验不调用模型。新版本主入口在批处理返回非零后先保存真实服务计数，再做此校验并决定是否进入未修改的官方 judge，避免末尾校验错误丢失计数；历史冻结入口继续保留旧行为，须独立记录仅评分续跑。若历史终态计数确已丢失，记录已观测下界及缺口，禁止补造精确数值或宣称已通过全部最终验收。

每轮先评测 baseline，再进行真实训练，最后启动独立推理进程重载 checkpoint 并评测 dev。结果、配置、请求、任务结果、AReaL 原生轨迹、训练日志、checkpoint manifest、参数变化和 incumbent 选择保存在配置的 `output_root`。相同 run ID 不重复提交 allocation；已有阶段结果需通过内容哈希校验。发现仍存活的旧进程时拒绝重启，失败阶段保留证据并使用新 run ID。

提交通过 [`sbatch --hold`](https://slurm.schedmd.com/sbatch.html) 保证作业在初始回执保存前不会开始执行。初始回执写入失败时取消该作业；取消未确认时，报错保留 job ID 供核查。放行状态尚未确认的回执记录 `release_pending`，重试同一 run ID 会核查并恢复原作业；放行响应丢失或后续回执写入失败也不会重复提交。若提交响应丢失且没有回执，保留的提交目录会阻止自动重提，应先按 run ID 对应的 Slurm 作业名核查 held 作业。

候选仅在相同 dev 和 harness 条件下严格提高成绩时替换 incumbent。最终候选须在完整830题评测前冻结；原 H0、相同推理预算、官方索引和官方评分的 Accuracy 至少125/830，并补充其他种子、配对统计、Evidence Recall、搜索次数、真实 token、延迟和全部 GPU 成本。修改 harness 时另做归因对照。

冻结最佳模型后，使用同一份数据/索引配置单独评测外部 test。配置的 `model` 指向待评测权重，`project` 指向包含此入口的冻结源码；分别提交初始模型和最佳模型。该模式申请2张 GPU，复用已有独立重载与评测阶段，完整记录 test split，且不会训练或修改 incumbent。测试结果用于最终泛化报告。开发集的独立复测可使用 `--evaluate-only dev`。

```bash
python -m examples.search.pipeline --submit --evaluate-only test \
  --config /path/to/frozen-heldout-config.json --run-id heldout-best-seed42 \
  --image /path/to/areal-sglang.sif \
  --time-limit 7200
```

全量评测使用 `FinalEvalConfig` JSON，分别指定模型、原始 tokenizer/generation-config、模型文件 manifest 及其 SHA256、`asset_lock` 及其 SHA256、原检索环境 Python 和 vLLM Python。asset lock 引用并校验原模型、embedding、judge、官方索引、语料、题目/标签、snippet tokenizer、Qwen-Agent 和冻结评测源码的文件清单。题目和标签仅用于全量评测，不能成为训练输入。官方索引文件另按公开 LFS SHA256 校验来源。提交脚本传入 host 核验的 `rsi` allocation，使用3张 GPU。

训练镜像使用 HF5，最终 vLLM 镜像使用 Transformers4.57。HF5 将 `rope_theta` 移入 `rope_parameters`；直接使用该 checkpoint 时，旧环境会采用错误的默认 theta。先在项目环境中生成兼容导出，补齐对应的 legacy RoPE/dtype 字段，并检查每个权重文件的 SHA256 未变。原始 checkpoint 保留，导出 manifest 与来源记录位于新目录旁。最终配置的 `model` 和 `checkpoint_manifest` 指向导出目录及其 manifest；评测仍使用原冻结 H0 运行时和 asset lock。

```bash
python -m examples.search.final_evaluation \
  --export-checkpoint /path/to/original/checkpoint.json \
  --source-sha256 ORIGINAL_CHECKPOINT_MANIFEST_SHA256 \
  --output /path/to/new/inference-export
```

```bash
python -m examples.search.final_evaluation --config /path/to/frozen-final-config.json
python -m examples.search.analysis \
  --baseline /path/to/baseline/official-results \
  --candidate /path/to/candidate/official-results \
  --queries /path/to/official/queries.tsv --output /path/to/paired-report.json
```

基线和候选沿用同一份 asset lock，候选 checkpoint 使用独立冻结的模型 manifest。主种子42，补充种子43；每次使用新的 output 目录。评测不会覆盖已有目录，正式结果必须覆盖全部830题，并通过 judge 解析与运行故障检查。

`make check` 检查 CPU 架构、测试、Ruff、Mypy 与 UI；原生轨迹读取和任务关联在 CPU 测试中验证。小文件或 mock 测试不提供模型学习的证据；真实 step、权重变化、完整保存、独立重载与评测必须由 GPU 产物证明。
