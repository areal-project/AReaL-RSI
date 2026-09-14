# CUHK-X 训练与模型接入

Research 可修改 `seed/` 的模型、训练和预测代码；训练产物冻结后，由独立评分器计算本地留出 clip accuracy。该成绩不是 Kaggle leaderboard 分数。

## 环境与数据

用户准备比赛数据与训练环境。真实训练和推理通过 Docker 运行，镜像内安装 [requirements.txt](requirements.txt) 及匹配 CUDA 的 torch；主控与 Docker 挂载路径需位于同一主机。当前 wrapper 不支持 SSH runner。

数据根目录包含 `Depth_Color/<action_id>_<name>/<user>/<trial>/<frame>.png`，动作 ID 为 0–39。训练、search 和 final subject 必须互斥，划分由用户指定。例如：

```bash
uv run python -m areal_rsi.adapters.cuhkx prepare \
  --data /datasets/HAR/data --output .rsi/cuhkx-prepared \
  --validate-subjects 3 24 --final-subjects 4 23 \
  --modality Depth_Color --max-frames 16
```

其他目录布局可提供 `--manifest clips.csv`，列为 `clip_id,subject,label,modality,frames`；frames 是相对数据根目录路径的 JSON 数组。准备结果中的 train 含标签，validate/final 只含匿名输入，私有标签留在可信侧。

复制 [operator.example.json](operator.example.json) 到候选目录外，填写 prepared_data、已安装的 Docker 镜像、GPU ID、资源与训练上限。配置不属于候选的可修改内容。

## 启动与产物

可在[工作台](../../README.md)直接提供环境与目标，也可生成配置：

```bash
uv run python scripts/configure_research.py --example cuhkx \
  --operator /absolute/path/operator.json --output /absolute/path/research.json --max-agents 2
uv run --env-file .env --locked rsi workbench --config /absolute/path/research.json
```

生成器预检 search/final 身份并固定镜像，不执行候选或产生分数。实际训练入口是 `run.py train --candidate <workspace> --output <new-output> --config <operator.json>`；完成后用 `collect_job_output(destination="trained")` 收集产物，候选中应有 `trained/checkpoint.pt`。

验证入口是 `run.py verify --candidate <snapshot> --output <new-output> --config <operator.json> --phase search|final`。训练只挂载 train，推理只挂载匿名输入，禁用网络；宿主评分器读取私有标签。phase 为 `search → validate`、`final → final`，最终验证使用同一冻结候选。stdout 不是成绩，失败的 `evaluation.json` 不更新 best。

种子使用 Depth_Color，无 RGB 摄像头模态或预训练权重。候选推理包总计不超过 100,000,000 字节；测试提交需使用实际 sample_submission.csv 的顺序。导出命令见 `python -m areal_rsi.adapters.cuhkx --help`。合成 CPU 冒烟可显式使用 local runner，真实数据仍需 Docker。
