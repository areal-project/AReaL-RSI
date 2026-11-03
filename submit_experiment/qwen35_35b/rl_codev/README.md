# Qwen3.5-35B CodeV 单轮 RL

本目录是独立的 Agentic RL 入口。生成通过 AReaL 的 inline Agent / OpenAI proxy，
每条样本只请求一次模型；随后上传完整回答到 Arena，等待独立 judge 的 reward。
proxy 负责 session、tokens、logprob、版本和训练张量，沿用已有失败 session 释放机制。
不使用 SWE 的 message preprocessors 或 prefix matcher，避免修改 CodeV 的原始提示。
不使用 RLVRWorkflow，也不调用 Arena 的模型网关生成。

固定数据来自 [codev-r1-rl-20261028-r1-train@1.0.3](https://arenaonline-prod.alipay.com/registry/benchmarks?key=codev-r1-rl-20261028-r1-train&version=1.0.3)，
共 3032 道 Verilog 题，公开快照保留该版本全部题目及发布顺序；训练默认 shuffle=true。
原 Benchmark 已排除有问题的 `train-126593`，本入口不额外筛选。
上传协议要求 UTF-8 `response.txt`，最大 1 MiB；这是完整回答，不是 git diff/patch。
保留模型的 think/answer 标签与 Verilog fenced block；不提取、修复或改写候选代码。
若 proxy 将 reasoning 与 content 分开返回，Agent 用 think 标签恢复回答文本。

三个配置分别为：

- `base.yaml`：固定镜像、DP2 × CP4 / EP8、rollout TP8、256K 上下文、Slurm 与运行环境。
- `params.yaml`：采样、训练、优化器，以及 `codev` 下评分并发、轮询与超时等可调参数。
- `arena.yaml`：精确 Benchmark 版本及 content hash、upload Harness 与 reward 引用。

默认训练 batch 64、queue 128、32 组并发、每组采样 12、最小有效组 8。
SGLang `max_running_requests` 和 decode graph 上限经 OmegaConf 自动计算为 384。
Agent 的一次请求为 `n=1`，组内 12 条由 AReaL 调度，不会再次乘 12。
Adam eps=1e-8，CPU staged offload 和 chunked logits 默认开启，R3 关闭。
默认采样 temperature=1、top_p=1、top_k=1000000、最大输出 65536 tokens。
本地 Agent 显式传递这些值，Arena upload Harness 不覆盖采样。
评分并发默认 32（每个 rollout worker），与生成并发上限分开控制。
生成超时默认 12000 秒；评分总预算默认 900 秒，含派生 Benchmark 准备、排队和执行，
从获得评分并发槽后计时。API 单次超时 60 秒。

## 提交

与 `rl/` 一样，需要 Python 3 与 PyYAML；运行依赖来自原有两个 SIF 及 overlay。
`AREAL_DIR` 从环境变量或 credentials.env 读取，不写进 YAML。
凭据优先级为 `--credentials-file`、`CREDENTIALS_FILE`、本目录 `credentials.env`、
模型父目录 `credentials.env`。真实文件不会复制到实验目录；W&B key 和 URL 均存在时自动开启。

```bash
cd /absolute/path/to/AReaL-RSI/submit_experiment/qwen35_35b/rl_codev
cp credentials.env.example credentials.env
chmod 600 credentials.env
# 填写 AREAL_DIR、ARENA_OPENAPI_BASE、ARENA_OPENAPI_TOKEN，以及可选 W&B 配置。

./submit.sh --dry-run --experiment-name qwen35-codev --trial-name inspect
./submit.sh --check-only --experiment-name qwen35-codev --trial-name preflight --reservation rsi
./submit.sh --experiment-name qwen35-codev --trial-name trial01 --reservation rsi
```

省略 `--reservation` 使用公共池，显式值传给 controller、actor、rollout。
`--params` 和 `--arena` 可指定替代配置。
`--check-only` 同步等待 CPU 作业完成，校验所有公开 prompts、精确引用、hash 和上下文预算；
不调用模型、不运行 judge、不写 Arena registry、不申请 GPU。成功后删除临时目录，失败保留日志。
正式实验继续申请一个 CPU controller 与一个共卡 actor/rollout GPU 节点。

## 数据与评分流程

CPU 预检只向训练数据投影 system/user 原始消息、任务 ID、Case/Harness/reward 精确版本及 hash。
Case API 返回的 oracle、参考实现、testbench 不写入公开 JSONL，不传给 Agent 或 proxy。
每条消息重建后核对原始 `prompt_sha256`，超长直接报错，不静默截断或替换。
预检生成的 `public-dataset.jsonl` 在训练时复用，不再重新选择数据。

Arena 当前 Job 接口按整个 Benchmark 展开。为保证一条回答只评分对应题，实际评分时按需创建
`codev-upload-<binding hash>@1.0.0` 单题派生 Benchmark，仅引用原 Case、upload Harness 和 reward；
原始 `1.0.3` Benchmark 不变。派生版本存在时先读取验证再复用。
因此正式训练使用的 Arena token 需要 Benchmark 创建/发布与 Job 提交权限。
不创建 3032 题完整评测 Job，不修改共享 Stream，不需要新增模型路由。

Agent 上传 `CODEV_RESPONSE_B64` 及回答 SHA、task ID、prompt SHA、空 baseline SHA，
使用已发布 `codev-r1-rl-20261028-r1-upload@1.0.0` 将回答落入评分输入；
`codev-r1-rl-20261028-r1-judge@1.0.2` 在独立 sandbox 执行。
评分结果必须匹配实际 Task、Case 和回答 SHA。
`accepted` 得 1；已知候选错误（格式、语法、答案、候选执行超时等）得 0。
`SETUP_FAILED`、`EVAL_FAILED`、API/平台超时、未知或冲突结果都作为基础设施错误，
通过 WorkflowContractError 中止当前训练批次，不冒充 reward=0。
Benchmark 的聚合策略不会覆盖这一训练侧判断。

GET 可有限重试；模型请求和创建评分 Job 的 POST 不自动重试。
上传响应丢失时保留 intent 并报错，避免因平台自动生成新 Job ID 而重复评分。
拿到 Job ID 后，本地评分超时或取消会尝试停止该 Job。

## 日志与验证

日志使用 `/storage/openpsi/experiments/logs/<user>/<experiment>/<trial>/`：
`controller-*.log`、`main.log`、actor/rollout 日志、`preflight.json`、
`public-dataset.jsonl`、配置与源码快照，以及 `arena_results/<binding>/` 的回答、状态和结果。
该目录没有凭据文件；不要将整个日志目录当公开数据发布。
源码脚本会复制到运行目录，并将运行目录加入 controller/worker PYTHONPATH，
因此不要求把本目录安装成 AReaL 包。

在实际 actor/rollout 镜像中执行 CPU 预检；离线测试使用：

```bash
python3 -m pytest test_codev.py
```

预检与 HTTP mock 测试不代表完整 GPU 训练或真实 judge 链路已通过。
