# RSI 任务接入与会话 API

系统入口是 `infra.research.ResearchSystem`。未绑定任务的 `/rsi` 继续使用原来的
Interaction → Leader 流程；任务绑定增加候选和评测能力，不改变原生历史的归属。

## 启动任务

控制节点需要已有 RSI Python 环境、Codex、可达的研究模型 API 和 Slurm 客户端。
TIR/Search 的 GPU 依赖在各自的 Apptainer 运行环境内。凭据通过私有 env 文件读取，
不放入任务配置或 recipe。以下示例在已安装本仓库的源码 checkout 中运行。

```bash
areal-rsi research \
  --problem examples.tir.problem:TIRProblem \
  --config /absolute/tir-experiment.json \
  --objective /absolute/objective.md \
  --root /absolute/runs/tir-001 \
  --env-file /absolute/private.env
```

TIR 配置使用 `examples/tir/experiment.json` 的结构，需要实际 AReaL 源码、基模型、
GPU Python、已有 benchmark 和可用镜像。`image` 和 `slurm` 在配置顶层。
准备原始 ToRL parquet 的入口仍为 `python -m examples.tir.benchmark --source ... --output ...`，
需在带 datasets/pyarrow 的环境运行；准备好的 benchmark 再交给研究入口。

Search 使用 `examples.search.problem:SearchProblem`，完整模板见 `examples/search/research.json`。配置结构为：

```json
{
  "search": {"...": "SearchConfig 的字段"},
  "image": "/absolute/training.sif",
  "slurm": {"reservation": "rsi", "partition": "all", "cpus": 32, "memory_gb": 256},
  "min_rounds": 1
}
```

`search` 对象按 `examples/search/experiment/config.py:SearchConfig` 填写，
包括模型、冻结源码 project、AReaL 环境、数据/索引、embedding 和检索 Python。
示意对象不是可直接运行的完整配置。Search recipe 是 `{"training": {...}}`，
字段使用同文件的 `TrainingConfig`。

启动首先打印 `run_id`，再准备冻结协议并执行 valid 基线，然后交接真实 Leader。
原始 objective 正文会被保存。恢复时沿用绑定、配置和目标，不能同时传入替代配置：

```bash
areal-rsi research --root /absolute/runs/tir-001 --resume run-XXXXXXXXXXXX --env-file /absolute/private.env
areal-rsi status --root /absolute/runs/tir-001 --resume run-XXXXXXXXXXXX
areal-rsi finalize --root /absolute/runs/tir-001 --resume run-XXXXXXXXXXXX --candidate r2
```

status 不启动模型；finalize 不启动研究 Agent，只针对已冻结的候选执行独立 test。
TIR 保留重复采样，最终结果包含按 question 聚类的配对统计。Search 默认 test 是外部数据
held-out；提供 `official: FinalEvalConfig` 和 `official_image` 后，finalize 使用固定 H0、
三 GPU、官方 830 题与 judge，保留原始评分产物及 checkpoint 兼容导出证据。
最终 `target_met` 只表达配置中的主指标增益目标，不替代 objective 的全部验收项。

## 任务实现

任务类通过 operator 指定的 `module:Class` 加载，不在 infra 中硬编码示例名。
类声明 `config_type`、`recipe_type`，构造函数接收已解码的配置 dataclass，并实现：

| 接口 | 任务职责 |
| --- | --- |
| `prepare(root) -> ProblemSpec` | 本地准备资源，声明基线、固定比较条件、锁定文件及源码范围 |
| `build_candidate(context, recipe) -> CandidatePlan` | 准备候选数据/产物，返回父子 solution 与可选训练作业 |
| `build_evaluation(context) -> EvaluationPlan` | 返回评测作业、预期样本 ID、输入引用和独立题目分组 |

准备和构造计划可以写本地文件，远程作业必须由 infra 执行。任务无需实现会话驱动、
候选状态机、Slurm 提交回执、结果缓存或选择冻结。三个接口的实际实现见
`examples/tir/problem.py` 和 `examples/search/problem.py`。

`CandidatePlan` 没有训练作业时直接使用冻结 solution，支持 harness/artifact 演化。
有训练作业时提供 AReaL runtime 与 JobSpec，worker 仅在真实训练和 checkpoint 写完后发布
`TrainingReceipt`；infra 校验步数、权重分片和内容摘要。评测 worker 发布完整
`samples.jsonl`，每行是 `EvaluationSample`。任务错误计分；执行故障使作业失败。

比较条件与允许演化的对象分开保存。模型研究固定 harness；harness 研究将其变化记录为
solution 内容，并保持外部数据、判分和预算约定一致。主指标及 maximize/minimize 方向
由 `ComparisonContract` 声明。任务自定义配置不得作为未解码的字典进入内部状态。

## 运行与恢复

每个 run 的任务记录位于 `sessions/<run_id>/problem/`：配置、protocol/freeze、候选、
源码归档、执行回执、轨迹、selection、final.json 和 report.md。会话生命周期仍只由
SessionRecord 管理，进程生命周期由 JobRecord 管理，候选状态由 CandidateRecord 管理。

提交候选先保存请求并返回句柄，本地数据准备、训练和验证在后台运行；准备失败也保留可查询记录，
不自动重放不完整的准备步骤。Leader turn 结束后，宿主等待作业完成再
自动继续，避免模型空转。使用候选查询工具获取反馈和 valid 证据。失败或控制器重启后，
显式调用评测工具恢复候选；已完成训练/评测复用经过校验的产物。重试失败的 valid 阶段需
显式 `retry=true`，创建新的 attempt，保留旧记录。
接受已有计划的恢复请求后，工具返回待执行状态并清除旧错误；后续故障通过后台反馈报告。

Slurm 使用 held submission，远端 ID 和初始回执保存后才 release。响应丢失但回执存在时
重连同一个 job；提交结果未知时保留提交目录并拒绝自动重提，需要按记录中的作业名核查。
普通控制器退出/关闭连接保留已提交 Slurm 作业；暂停会话也不取消外部作业。
显式 stop 只取消该 run 回执中记录的作业。取消请求与确认资源释放分别记录。
本地进程中断会清理自己的进程树，恢复时不会盲目重放。

`SlurmResources.node` 可指定一个节点，避免在已有异常占用的节点上重放实验。
GPU 容器入口共用 `infra/worker.sh`；模型加载前保存 `gpu-allocation.json`，核对分配的
设备与 CUDA 可见设备，并拒绝已有超过 1 GiB 显存占用的 GPU。检查失败保留设备和显存
记录，作业失败退出，不通过缩小训练微批或丢弃样本来绕过占用。

固定评测源码、配置和输入发生变化会拒绝继续。可编辑的任务源码可以在轮次之间修改，
每个候选记录对应快照，并检查执行阶段前后源码一致。历史实验使用原始冻结运行时，
不改写旧 protocol、source-lock、候选摘要或产物。
源码快照始终排除 Python 字节码缓存，不依赖运行目录是否携带 `.gitignore`；公共 worker 禁止写入字节码。

## 用户方法与 Agent 工具

原有 create/open/detail、send_message、enter_rsi/start、pause/resume/stop/chat、
answer、events、set_budget、learn 等方法保持。`create(config, problem=...)` 可选绑定任务。
新增方法均携带 run_id：prepare_problem、problem_detail、submit_candidate、candidate_status、
evaluate_candidate、select_candidate、finalize_problem。

绑定任务才注册 rsi_submit_candidate、rsi_candidate_status、rsi_evaluate_candidate、
rsi_select_candidate。工具从当前 thread/turn 校验 Leader 身份，JSON 请求复用 api/control.py。
选择必须有完整且可比较的 valid 证据，满足最低轮次和改进要求，且是已完成候选中的最佳。
完成工具会重新校验冻结选择；文字 evidence 不能代替实际结果。最终 test 不作为 Agent 工具开放。

API 数据放 api/records.py、research.py、execution.py，协议放 interfaces.py，控制请求放
control.py。infra 不静态导入 examples 或 UI；UI 仅调用 ResearchSystem。
其余保留协议不代表已有实现，不构成第二条任务接入路径。
