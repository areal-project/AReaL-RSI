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

下面假设 3、24 是本地训练数据中实际存在的 subject。用户在启动 run 时决定正式划分和采样预算。

```bash
uv run python -m areal_rsi.adapters.cuhkx prepare \
  --data /datasets/HAR/data --output .rsi/cuhkx-prepared \
  --validate-subjects 3 24 --modality Depth_Color --max-frames 16
```

准备目录不可覆盖，每个 clip 均匀选取至多 16 帧并复制字节。大量图像会占磁盘；`max-frames` 一旦固定就是验证协议的一部分，不能在比较候选时改变。

```text
cuhkx-prepared/
  train/manifest.json       # 帧路径、clip ID、训练标签
  train/inputs/             # 仅训练者的数据
  validate/manifest.json    # 匿名 clip ID 和帧路径，不含标签/subject
  validate/inputs/          # 随机 ID、重新命名的帧，不保留标签目录
  private/validate_labels.json
  private/provenance.json   # 原始路径与划分归档
  protocol.json            # 划分、输入摘要、数据类型、协议身份
```

不同布局可用 `--manifest clips.csv` 替代扫描。CSV 列必须是 `clip_id,subject,label,modality,frames`；`frames` 是 JSON 字符串数组，每项相对 `--data`。例如 Python 标准库可写入一行：

```python
writer.writerow(["clip-A", 1, 0, "Depth_Color", '["custom/one.png", "custom/two.png"]'])
```

路径穿越、符号链接、重复 clip / 帧、不合法标签和空划分会被拒绝。
模态名称必须显式指定；读取现成图像为三通道像素不等于引入 RGB 摄像头模态。

## 2. 训练候选并保存 checkpoint

复制 `seed/` 到当前 Research Agent 的候选工作区，再调用其中的入口。下列本地命令用于开发联调：

```bash
.rsi/envs/cuhkx/bin/python examples/cuhkx/seed/train.py \
  --manifest .rsi/cuhkx-prepared/train/manifest.json \
  --output .rsi/cuhkx-job-001 --device cpu --epochs 10
```

输出 `checkpoint.pt`、`config.json`、`training.jsonl`。checkpoint 包含所有推理权重、40 类映射、架构与预处理配置，不包含 optimizer。
正式运行将 `--device` 改为分配的 GPU。每次作业使用新的输出目录；短训练的诊断结果与正式预算的结果分开比较。
此种子不提供训练中断后续训，终止训练后重新提交一个新作业；已完成的 checkpoint 可以独立加载用于预测。

## 3. 匿名推理与可信评分

```bash
.rsi/envs/cuhkx/bin/python examples/cuhkx/seed/predict.py \
  --manifest .rsi/cuhkx-prepared/validate/manifest.json \
  --checkpoint .rsi/cuhkx-job-001/checkpoint.pt \
  --output .rsi/cuhkx-job-001/validate_predictions.csv

uv run python -m areal_rsi.adapters.cuhkx score \
  --predictions .rsi/cuhkx-job-001/validate_predictions.csv \
  --labels .rsi/cuhkx-prepared/private/validate_labels.json \
  --checkpoint .rsi/cuhkx-job-001/checkpoint.pt \
  --output .rsi/cuhkx-job-001/verify.json
```

每个 clip 只产生一个预测，评分器要求所有 ID 完整且唯一、预测为 0–39 整数。它重新计算 accuracy，记录 checkpoint 和预测文件摘要；不导入候选模块，不反序列化权重，不读取训练 stdout 的分数。
种子和评分器都检查单一推理 checkpoint 不超过 100,000,000 字节。扩展为多 checkpoint 或额外权重文件时，调度器须检查整个推理产物包的总量，不能仅传一个小文件通过检查。

**候选作业的权限必须由 runner 落实**：训练只挂载 `train/`，推理只挂载 `validate/`、候选代码和固定 checkpoint；禁止访问原始数据、`private/`、宿主密钥及评分器写目录，推理不开放网络。
上述命令直接在宿主运行，仅供人工控制的种子联调，不构成进程隔离。正式评测必须从冻结的代码/权重快照执行，并由宿主固定 manifest 和训练预算；报告里的摘要本身不能证明候选没有越界。

## 4. 生成提交文件

比赛测试数据是未标注的匿名 clip。使用下载的实际 `sample_submission.csv`，不要自行推测列名和顺序。
工具支持 `<sample path>/Depth_Color/<frames>` 的测试布局；若实际解压层级不同，需要整理或显式提供同格式的公开 `manifest.json`，不应自动匹配别的模态或丢掉样本。

```bash
uv run python -m areal_rsi.adapters.cuhkx prepare-test \
  --data /datasets --sample /datasets/sample_submission.csv \
  --output .rsi/cuhkx-test --modality Depth_Color

.rsi/envs/cuhkx/bin/python examples/cuhkx/seed/predict.py \
  --manifest .rsi/cuhkx-test/manifest.json \
  --checkpoint .rsi/cuhkx-job-001/checkpoint.pt \
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
uv run pytest tests/test_cuhkx.py
```

冒烟脚本生成明确标记的合成图像，真的训练 CNN、保存并重新加载 checkpoint、运行匿名预测、按 clip 评分、导出提交 CSV，最终保存 `smoke.json`。
它只证明代码路径可运行，任何数值都不是 CUHK-X 成绩。真实比赛文件和 GPU 运行需要用户资源到位后再做一次小规模验收。
