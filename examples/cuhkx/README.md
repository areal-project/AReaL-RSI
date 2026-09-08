# CUHK-X Small Model 示例

这个示例提供可修改的训练/推理代码，以及独立的数据准备、验证和提交文件导出工具。
Research Agent 可以修改 `seed/` 中的模型、训练过程和预测逻辑；RSI 调度器负责冻结候选版本、启动作业和读取报告。
数据准备和 `areal_rsi.adapters.cuhkx` 的评分代码属于受信任侧，不放进候选目录。

种子模型是从零训练的小 CNN：读取 Depth_Color 图像，编码各帧，在 clip 内聚合后输出 40 类概率。
它不使用预训练权重、不下载模型、不使用 RGB 摄像头数据。IR / Thermal 可以显式选择；此种子未实现 IMU / Radar / Skeleton 解码器，研究者可以在候选源码中增加。
种子只是一个可执行起点，不是预先限定的研究方向，也不保证比赛成绩。

## 环境与真实数据

- 主控的数据准备和评分只需要 Python 标准库，训练环境单独安装 [requirements.txt](requirements.txt)。
- 用户需要下载并解压 [比赛数据](https://www.kaggle.com/competitions/cuhk-x-competition-small-model-track/data)，这里不自动接受下载平台协议或下载大文件。
- 数据扫描器对应固定的 [官方图像加载器](https://github.com/openaiotlab/CUHK-X/blob/df03910a6960db5af9179e370e130bf61d9616d0/SM/Code/rgb/train_models_cross_multi.py)：`HAR/data/Depth_Color/<action_id>_<name>/<user>/<trial>/<frame>.png`。支持 jpg/jpeg/png。
- `--data` 指向包含 `Depth_Color` 的目录。`user` 接受数字或 `user1` / `user_1` / `user-1`。其他发布版本的布局应提供显式 CSV，扫描器不会猜测标签或 subject。
- 原始动作 ID 固定为 0–39。验证 subject 从已有训练 subject 中选出，不能使用比赛测试者或测试标签。分数是本地留出 clip accuracy，不是 Kaggle leaderboard 分数。
- 用户已经确认的 LLM 辅助研发授权按该授权执行；导出仍保持比赛的非 RGB、模型体积等产物约束。

```bash
uv venv .rsi/envs/cuhkx --python 3.12
uv pip install --python .rsi/envs/cuhkx/bin/python -r examples/cuhkx/requirements.txt
```

NVIDIA GPU 环境按 [PyTorch 官方安装说明](https://pytorch.org/get-started/locally/) 选择匹配 CUDA 的 wheel。
`--device cuda:0` 在 CUDA 不可用时会失败，不会默默改成 CPU。CPU 可跑冒烟测试。

## 1. 固定数据与验证划分

下面假设 3、24、4、23 是本地训练数据中实际存在的 subject。用户决定正式划分和采样预算：3、24 用于迭代验证，4、23 留作最终确认，其余 subject 训练。三组必须互斥且非空。

```bash
uv run python -m areal_rsi.adapters.cuhkx prepare \
  --data /datasets/HAR/data --output .rsi/cuhkx-prepared \
  --validate-subjects 3 24 --final-subjects 4 23 \
  --modality Depth_Color --max-frames 16
```

准备目录不可覆盖，每个 clip 均匀选取至多 16 帧并复制字节。大量图像会占磁盘；`max-frames` 一旦固定就是验证协议的一部分，不能在比较候选时改变。

```text
cuhkx-prepared/
  train/manifest.json       # 帧路径、clip ID、训练标签
  train/inputs/             # 仅训练者的数据
  validate/manifest.json    # 匿名 clip ID 和帧路径，不含标签/subject
  validate/inputs/          # 随机 ID、重新命名的帧，不保留标签目录
  final/manifest.json       # 独立 subject，仅用于最终确认
  final/inputs/
  private/validate_labels.json
  private/final_labels.json
  private/provenance.json   # 原始路径与划分归档
  protocol.json            # 划分、输入摘要、数据类型、协议身份
```

不同布局可用 `--manifest clips.csv` 替代扫描。CSV 列必须是 `clip_id,subject,label,modality,frames`；`frames` 是 JSON 字符串数组，每项相对 `--data`。例如 Python 标准库可写入一行：

```python
writer.writerow(["clip-A", 1, 0, "Depth_Color", '["custom/one.png", "custom/two.png"]'])
```

路径穿越、符号链接、重复 clip / 帧、不合法标签和空划分会被拒绝。
模态名称必须显式指定；读取现成图像为三通道像素不等于引入 RGB 摄像头模态。
`--final-subjects` 对单独的数据准备是可选项；没有它时，最终确认和下面的完整研究配置生成会明确失败，不会复用 validate。

## 2. 配置可信 runner 和研究入口

复制 [operator.example.json](operator.example.json) 到候选目录外，填写 `prepared_data` 的绝对路径、已经安装的 Docker 镜像名和 GPU ID。镜像内需要 Python 与 [requirements.txt](requirements.txt) 中的训练依赖；CUDA wheel 和驱动匹配，由操作员准备。主控和 Docker 挂载路径应位于同一主机，当前 CUHK-X wrapper 没有 SSH runner。

```bash
uv run python examples/cuhkx/run.py describe \
  --config /absolute/path/to/cuhkx-operator.json --phase search
```

`describe` 检查固定数据、manifest 和图像摘要，并将 Docker 镜像解析为不可变的 image ID；不启动候选、不产生分数。它返回 `protocol_id`、`prepared_protocol_id`、`manifest_id`、`split` 和环境记录。此步骤不证明实际 CUDA 训练已经通过，仍需下面的小规模训练/验证验收。

推荐用统一生成器完成各阶段预检，并生成可直接加载的 ResearchConfig：

```bash
uv run python scripts/configure_research.py \
  --example cuhkx --operator /absolute/path/to/cuhkx-operator.json \
  --output /absolute/path/to/cuhkx-research.json --max-agents 2

uv run --env-file .env rsi workbench \
  --config /absolute/path/to/cuhkx-research.json --port 8765
```

打开 `http://127.0.0.1:8765`。`.env` 中设置 `RSI_API_KEY`，可用 `RSI_BASE_URL` 覆盖模型服务地址；生成器默认使用 `https://4ipic.com`、`gpt-6-astra`、`xhigh`，不会把 API key 写入配置。也可以使用 `rsi research --config ...` 启动命令行研究。

默认预算是 2 个 Research Agent、12 个节点、200 次模型调用、200 万 token、6 次正式验证、1 个并发作业、4 小时；使用 `--max-*` 参数调整。GPU 从 operator 的 `runner.gpu_ids` 读取，每个作业预留配置中的全部 GPU。`--target 0.7` 可显式设定目标 accuracy，不填则保留未设定；`--instructions-file /abs/notes.txt` 可加入研究方向、约束说明等自然语言要求。

生成器预检 search、final 和已配置的 diagnostic，将比较身份写入 `metadata.verification_identity`，不会从第一次候选结果反推可信身份。RSI 在用户已确定的目标、资源和协议内自主决定何时进行最终确认；seed 不会被当成已经评测的 baseline。改变数据、镜像、训练/验证配置后应重新生成配置并建立新的 run；生成器不会覆盖已有配置文件。

## 3. 训练、匿名推理与可信评分

`run.py` 是调度器调用的固定入口。手动小规模验收时，先复制 seed 到候选工作区：

```bash
cp -R examples/cuhkx/seed /absolute/path/to/candidate
uv run python examples/cuhkx/run.py train \
  --candidate /absolute/path/to/candidate --output /absolute/path/to/train-job \
  --config /absolute/path/to/cuhkx-operator.json
cp -R /absolute/path/to/train-job/trained /absolute/path/to/candidate/trained

uv run python examples/cuhkx/run.py verify \
  --candidate /absolute/path/to/candidate --output /absolute/path/to/search-job \
  --config /absolute/path/to/cuhkx-operator.json --phase search
```

每次使用新的作业输出目录。研究工作流中的 `train` 命令设置 `output_subdir="trained"`，Research Agent 通过 `collect_job_output(job_id, destination="trained")` 收集训练产物，再冻结候选并请求验证；不会把 `private/` 一起复制回候选。候选中必须存在 `trained/checkpoint.pt`。

可选的候选 `training.json` 用于申请 epochs、batch_size、frames、image_size、width、learning_rate、seed、threads；wrapper 检查 operator 的参数上限，并由 Docker 和独立超时机制限制 GPU、CPU、内存、进程数和运行时间。任意可编辑训练代码可能忽略 epoch 参数，因此实际资源限制依赖容器和超时，不能只相信训练日志。此 seed 不支持中断后续训，已完成 checkpoint 可以独立预测。

默认 Docker：训练只挂载 `train/`，推理只挂载匿名的 `validate/` 或 `final/`；候选快照只读，网络关闭，输出目录独立，不挂载原始数据、`private/` 或宿主密钥。宿主评分器读取私有标签，要求每个 clip 恰好一个 0–39 的预测，不导入候选模块、不反序列化权重、不读取 stdout 中的分数。完整推理候选包（代码、checkpoint 和附加文件）总计不得超过 100,000,000 字节。

`search → validate`、`final → final`，可选 `diagnostic → validate`。最终确认使用同一冻结候选和独立 final subjects，不再修改候选。wrapper 写入 `evaluation.json`，包含 `status`、`metric="accuracy"`、`score`、聚合 `metrics`、`simulated=false`、候选/checkpoint 摘要和各项评测身份；失败返回 `status="incomplete"`、`score=null`，不能更新 best。

`runner.kind=local` 只允许显式 `--allow-local` 且准备数据标记为 synthetic 的 CPU 冒烟；该模式不隔离候选，真实比赛数据不能用它绕过 Docker。生成器也支持同名参数，但只用于上述合成验证。

## 4. 生成提交文件

比赛测试数据是未标注的匿名 clip。使用下载的实际 `sample_submission.csv`，不要自行推测列名和顺序。
工具支持 `<sample path>/Depth_Color/<frames>` 的测试布局；若实际解压层级不同，需要整理或显式提供同格式的公开 `manifest.json`，不应自动匹配别的模态或丢掉样本。

```bash
uv run python -m areal_rsi.adapters.cuhkx prepare-test \
  --data /datasets --sample /datasets/sample_submission.csv \
  --output .rsi/cuhkx-test --modality Depth_Color

.rsi/envs/cuhkx/bin/python examples/cuhkx/seed/predict.py \
  --manifest .rsi/cuhkx-test/manifest.json \
  --checkpoint /absolute/path/to/candidate/trained/checkpoint.pt \
  --output .rsi/cuhkx-job-001/test_predictions.csv

uv run python -m areal_rsi.adapters.cuhkx export \
  --predictions .rsi/cuhkx-job-001/test_predictions.csv \
  --sample /datasets/sample_submission.csv \
  --output .rsi/cuhkx-job-001/submission.csv
```

缺少所选模态时明确失败，研究者可以增加缺失模态处理；不能静默省略 clip。导出保留原样路径与尾随 `/`，只生成 `path,prediction` 文件，不自动上传 Kaggle。
最终交付应一起保存候选源码、checkpoint、配置、数据/验证协议和运行环境。全量重训得到新 checkpoint 后，不能把原 checkpoint 的验证分数当成新 checkpoint 的分数。

## 合成数据上的真实 CPU 冒烟

```bash
.rsi/envs/cuhkx/bin/python examples/cuhkx/smoke.py --output .rsi/cuhkx-cpu-smoke
uv run pytest tests/test_cuhkx.py tests/test_cuhkx_runner.py tests/test_configure_research.py
```

冒烟脚本生成明确标记的合成图像，真的训练 CNN、保存并重新加载 checkpoint、运行匿名预测、按 clip 评分、导出提交 CSV，最终保存 `smoke.json`。
它只证明代码路径可运行，任何数值都不是 CUHK-X 成绩。真实比赛文件和 GPU 运行需要用户资源到位后再做一次小规模验收。

需要进一步验收整个研究应用的桥接时，使用已准备且包含独立 final 的合成数据：

```bash
uv run python scripts/smoke_cuhkx_bridge.py \
  --prepared /absolute/path/to/synthetic-prepared \
  --training-python /absolute/path/to/torch-env/bin/python \
  --output /absolute/path/to/new-bridge-smoke
```

此工具真实执行 `ResearchConfig → App.create → train → 收集 trained → 冻结候选 → search/final → 严格身份校验 → best/final → ZIP 导出`，并检查等待唤醒与会话保持；不构造模型 runtime，不调用模型 API。输出包含 `summary.json`、工具记录、完整 run 目录及 `verified-candidate.zip`。它需要额外的 Torch 环境，因此不纳入默认 pytest。
