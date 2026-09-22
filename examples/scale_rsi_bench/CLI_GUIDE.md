# 用 CLI 跑四个 Scale RSI Benchmark

四个任务：`nanogpt`、`opsd`、`jailbreak`、`swarm`。

## 1. 配置

```bash
cd /storage/openpsi/users/wanghaitao.wht/project/rsi/AReaL-RSI
uv sync --group dev
cp -n .env.example .env
chmod 600 .env
```

编辑 `.env`：

```dotenv
RSI_BASE_URL=https://4ipic.com
RSI_API_KEY=填写你的密钥
RSI_MODEL=gpt-5.6-sol
RSI_REASONING_EFFORT=xhigh
RSI_BACKEND=codex
RSI_RUNTIME_ISOLATION=linux-bwrap
RSI_WORK_ROOT=/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench
```

加载配置：

```bash
set -a; source .env; set +a

export REPO=/storage/openpsi/users/wanghaitao.wht/project/rsi/AReaL-RSI
export RUNTIME=/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench
export RUN_ROOT=/storage/openpsi/users/wanghaitao.wht/rsi-cli-runs/$(date +%Y%m%d-%H%M%S)
mkdir -p "$RUN_ROOT"
```

会话必须放在 `/storage`，不要放 `/tmp`；计算节点看不到登录节点的 `/tmp`。

## 2. 创建四个隔离会话

```bash
cd "$REPO"
for bench in nanogpt opsd jailbreak swarm; do
  python examples/scale_rsi_bench/sandbox/session.py \
    --runtime-root "$RUNTIME" create "$bench" "$RUN_ROOT/$bench"
done
```

验证隔离：

```bash
python examples/scale_rsi_bench/sandbox/session.py \
  run "$RUN_ROOT/opsd" -- \
  bash -lc 'test "$PWD" = /workspace; test ! -e /storage; ! srun --version >/dev/null 2>&1'
```

## 3. 启动 Codex CLI

以 OPSD 为例：

```bash
python examples/scale_rsi_bench/sandbox/session.py \
  codex "$RUN_ROOT/opsd" \
  --model "$RSI_MODEL" \
  --base-url "$RSI_BASE_URL" \
  '先读 instruction.md，只处理当前 benchmark。'
```

将 `opsd` 换成 `nanogpt`、`jailbreak` 或 `swarm` 即可启动其他任务。

Agent 最终应准备：

| 任务 | 产物 |
|---|---|
| NanoGPT | `submission/selection.json` |
| OPSD | 修改 `repo/`，可配置 `submission/recipe.env` |
| Jailbreak | `train.sh`，训练后产生 `submission/checkpoint` |
| Swarm | 优化代码，最终产生 `submission/graph.json` |

具体要求以工作区的 `instruction.md` 为准。

## 4. 在隔离环境内提交实验

Codex 修改完成后，直接在其终端执行：

```bash
rsi-submit
```

查询进度和结果：

```bash
rsi-submit status
```

`rsi-submit` 不是通用 Slurm 客户端。它只能请求宿主冻结当前工作区，并执行该会话登记的固定 benchmark 配方；不接受命令、路径、Slurm 参数或其他 benchmark 名称。`sbatch`、`srun`、`salloc` 和 `scancel` 在沙箱内仍不可用。结果写回 `/workspace/feedback/<提交编号>/`。

## 5. 让 RSI 自动运行

如果不需要手动操作单个 Codex：

```bash
python examples/scale_rsi_bench/sandbox/task.py opsd \
  --runtime-root "$RUNTIME" \
  --data-root /storage/openpsi/users/wanghaitao.wht/rsi-workbench-data \
  --port 28999 \
  --hours 3 \
  --max-jobs 5 \
  --max-agents 1
```

将 `opsd` 换成其他任务名即可。命令会创建隔离会话、启动研究，并打印研究 ID 和页面地址。
