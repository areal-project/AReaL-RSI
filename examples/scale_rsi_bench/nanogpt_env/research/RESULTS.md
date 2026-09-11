# nanoGPT 数据筛选优化结果（2026-09-11）

正式隐藏集 **PPL 296.22**，官方随机基线 **425.59**，降低 **30.40%**。评分方向为越低越好，未做额外归一化。正式验证器 `status=ok`、`flags=[]`，Slurm `COMPLETED / 0:0`。

共 **15 次训练：14 次完整 dev + 1 次最终隐藏集**。之前已有的随机 dev 基线 455.256 直接复用，未额外训练基线或做训练 smoke test。最终选择仅根据 dev 成绩冻结；隐藏集只评一次，此后未修改选择。

最佳 dev PPL **303.123**，相对随机 dev 基线降低 **33.42%**。dev 与隐藏集难度不同，两个基线分别比较。

## 最终产物

- [selection.json](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/submission/selection.json)：16,630 个互不重复的合法 ID，覆盖 12,500,293 tokens；实际打包前 12M tokens，消费 15,987 篇文档。
- [curate.py](curate.py) 与 [best_config.json](best_config.json)：可复现筛选器及最终参数；容器提交副本在 [submission](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/submission)。
- [claim.md](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/claim.md)：Hypothesis / Mechanism / Falsification / Transfer。
- [正式 result.json](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/results/verifier-ZRO71tXI/logs/verifier/result.json)、[正式日志](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/research/final-verifier.log)、[汇总 JSON](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/research/summary.json)。
- [全部尝试与指标](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/research/attempts.json)、[第 15 次正式评估记录](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/research/final_attempt.json)、[所有搜索配置](search_configs.json)。
- [冻结文件哈希](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/research/frozen_submission.json)、[从头复现检查](/storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/workspace/research/reproduction/check.json)。

大数据、缓存、选择列表及训练日志保留在 Git 外的运行目录；本目录保存算法、配置和本报告。

## 方法

只改变语料选择，训练器、模型、超参数和评估器均未改动。每轮固定 3000 步、seed 1337、12M tokens；所有 14 个 dev 作业均 `SUCCEEDED` 且 Slurm `COMPLETED / 0:0`。

1. 用公开 dev 的四个等长 250k-token 区块分别估计平滑 GPT-2 unigram 概率，用全池估计背景概率。没有网络请求、外部模型或隐藏集内容读取。
2. 文档按平均 `log(p_domain / p_pool) - 0.5 * log(p_domain)` 排序，加正文占比奖励和重复行惩罚。筛掉小于 200 / 大于 6000 tokens、常用词比例低于 0.12、英文字符比例低于 0.55、每 token 少于 3 字符、重复行比例大于 0.25 的文本。
3. 去除规范化全文重复；用 token 5-gram 的 bottom-16 哈希至少 8 个重合聚类近重复，每组最多选一篇。
4. 四领域分别保留约四分之一 token 配额。每选 500k tokens，按目前选择的词频重新打分，加入 `0.6 * mean(log(p_pool / p_selected))`，用 2M-token 背景先验稳定早期统计。
5. 累计选择至少 12.5M tokens，使用固定筛选 seed 42 打乱文档顺序，交给冻结打包器截取 12M。训练 seed 仍为 1337。

机制指标：公开目标到语料的 unigram KL 从全池 **0.5151** 降到选择集 **0.4017**；按 token 加权的长行正文比例约 **63.2% → 87.9%**，重复行比例约 **12.2% → 1.1%**。这些是文本统计代理，不等同于人工质量标签。

## 全部 dev 实验

| 次数 | 策略 | dev PPL ↓ | Slurm job |
|---|---|---:|---|
| 1 | `01_unigram_ratio` | 343.469 | 962954 |
| 2 | `02_bigram_quality` | 444.503 | 962955 |
| 3 | `03_bigram_ratio` | 409.024 | 962956 |
| 4 | `04_mixed` | 376.873 | 962957 |
| 5 | `05_ratio_pure` | 335.186 | 962962 |
| 6 | `06_ratio_strong` | 324.667 | 962963 |
| 7 | `07_wiki_technical` | 345.545 | 962964 |
| 8 | `08_global_ratio` | 621.855 | 962965 |
| 9 | `09_ratio_stronger` | 313.450 | 962968 |
| 10 | `10_short_documents` | 348.535 | 962969 |
| 11 | `11_near_dedup` | 330.440 | 962970 |
| 12 | `12_adaptive_coverage` | 311.507 | 962973 |
| 13 | `13_ratio_extreme` | 354.441 | 962974 |
| 14 | `14_strong_coverage` | 303.123 | 962977 |

第 15 次：正式隐藏集 **296.22**，Slurm job **962980**。直接使用第 14 次选择；没有根据隐藏集结果继续挑选。

主要结论：领域平衡与词汇覆盖有效；取消领域平衡、过度偏向稀有词、单纯偏爱高概率双词短语均退化。最终方案相对随机基线改善明显，但相邻方案几分 PPL 的差距尚不能视为统计显著；每个配置只有一次固定 seed 训练。

## 复现

在已准备好的环境，从仓库根目录运行（只生成选择，不训练）：

```bash
bash examples/scale_rsi_bench/nanogpt_env/scripts/slurm.sh \
  python /workspace/submission/curate.py
```

从原始固定资产重建全部特征并生成独立文件：

```bash
bash examples/scale_rsi_bench/nanogpt_env/scripts/slurm.sh \
  python /workspace/submission/curate.py \
  --features /workspace/research/reproduction/features.npz \
  --build --build-near \
  --config /workspace/submission/config.json \
  --output /workspace/research/reproduction/selection.json
```

已实际从头重建并验证：所有特征数组、近重复分组完全一致，输出选择列表逐字节一致。最终选择 SHA256：`9bf50c21991b90230b0b7317866a5dbaf26cb20d7bfdb225ce2998d48b6fd5cb`。

完整训练记录已保留；本次已用满 15 次预算，不再自动追加评估。
