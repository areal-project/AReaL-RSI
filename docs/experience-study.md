# 经验积累：真实 API 实验

本文记录历史实验，当时使用的不可变文档版本现已取消。当前实现按主题直接更新 Markdown；
下文的版本和路径仅描述当时实验，不代表当前存储方式。

本轮实验已结束。较大预算下，两次无文档尝试各通过 10/11 组检查，随后两次参考逐轮演进文档的
全新会话各通过 11/11；这是正向观察，但早期实验出现退步，样本也很少，尚不能证明稳定提升。
所有复杂任务尝试都因预算停止，不能把检查通过称为 Goal 完整交付。另一个小型真实 API 测试
已走通普通对话、目标完成、自动总结的完整链路。以下保留全部结果与限制。

## 固定方法

- 真实 API，模型 `gpt-6-astra`，reasoning effort `medium`，原生 Codex 0.162.1。
- 同一份事务性派生数据图规格，短预算组 Leader 预算 120,000 tokens / 300 活跃秒；外层最多 600 秒包含需求讨论。较大预算组参数另列。
- 每轮全新空工作区、全新原生会话，只复制 TASK.md。进入 Interaction，再 fork 为 Leader。
- 有文档组只得到通用方法目录和不可变版本路径；没有上一轮对话、源代码、答案或隐藏评分器。
- 工作停止后，在工作区之外运行同一独立评分器，11 类行为/性能检查。按停止时的落盘成果评分，
  不将 budget_limited 说成 Goal 完成；测试通过也不等于 Leader 已完成完整交付。
- `--learn` 在独立只读会话中根据执行历史与评分反馈提炼一般方法。文档版本与来源 thread 保留。
- 部分轮次并行运行，API 等待影响相同墙钟预算下可做的工作；本实验不是严格统计显著性研究。
- 表中 tokens 为 Leader 的原生累计用量，包含重复或缓存的输入，不含 Interaction 和独立总结的额外用量，不能直接当作账单。
  预算根据原生用量通知检查，一个请求结束时可能超过阈值；它不是供应商侧的硬限制。

规格 SHA-256：`40ab0cf8236a9637c1c9cfe930950b0158c017f0069d5019b14eaa1a67396aca`。

## 短预算探索

| 尝试 | 通过的检查组 | 失败组 | Leader tokens |
| --- | --- | --- | ---: |
| pilot-01 | 1/11 | operations, post_batch, rollback, same_value_rewire, aliases, cycle_batches, overflow, deep_chain, randomized, incremental_cost | 56624 |
| baseline-01 | 10/11 | post_batch | 44771 |
| learned-01 | 8/11 | post_batch, rollback, randomized | 46469 |
| baseline-02 | 11/11 | — | 29639 |
| learned-02 | 7/11 | aliases, overflow, randomized, incremental_cost | 31007 |
| baseline-03 | 9/11 | post_batch, rollback | 29434 |
| learned-03 | 6/11 | post_batch, rollback, aliases, overflow, randomized | 32889 |

pilot-01 在预算中断时暴露了旧实现等待取消确认过久的问题，源文件仍是未完成状态。
它用于发现宿主缺陷并产出第一份经验，不能与已修复中断后的结果直接作因果比较。
baseline-01 / learned-01 的旧 runner 在预算后调用 pause，所以报告 status 为 paused；
实际 Leader 活跃时间均约 300 秒。后续 runner 保留 budget_limited 状态。

learned-01 的首次总结因模型抄错版本哈希被安全拒绝。实现随后改为宿主固定版本并做冲突校验；
再次总结成功，没有人工改写文档内容。learned-02 暴露了全量复制建议与增量性能的冲突，
新版总结提示词要求审查旧经验、区分证据和推测、保留所有验收约束。

无文档组已出现全部检查通过，而前两份文档反而有退化，不能只展示有利轮次或声称单调提升。
随后吸收成功案例的验证经验，再在全新会话中检查后续版本，结果如下。

## 成功经验的固定版本验证

前三次基于失败记录的文档演进成绩为 8/11、7/11、6/11，没有改善。
随后让独立整理会话复盘 baseline-02 的全部通过结果，得到版本
`25b2af2023d9fee4e8472b268b6331fdf7451ff11e258d06c785931219a9b7ce`。
这一步的动机是比较经过成功案例校正的经验，而非假定更多失败摘要一定更好。

该文档冻结到独立数据目录 `/tmp/rsi-experience-validation-20261015`，继续使用完全相同的任务、
预算、模型和评分器。按计划固定运行 learned-04、05、06 三次全新会话，没有根据某一次成绩增减样本，
不在这三次验证之间更新文档。与三次无文档对照共同报告。该版本没有题目实现代码、接口名或答案。

### 评分覆盖的额外审查

源码审查发现，baseline-02 虽然通过 11 类检查，更新路径仍有全量节点集合/候选字典构建和
全量缓存复制；baseline-01 也会构建全量 ID 集合。它们在指定的时间阈值下通过，
但不完全满足规格“不反复处理无关状态”的复杂度要求。因此 11/11 仅表示该评分器通过，
不能称为完整任务验收通过。固定版本的三次验证仍使用原评分器，避免中途换指标；
源码审查发现作为独立限制记录，不隐藏也不改变既有分数。

固定版本验证结果：

| 尝试 | 检查组通过 | 失败组 | Leader tokens |
| --- | --- | --- | ---: |
| learned-04 | 11/11 | — | 49262 |
| learned-05 | 4/11 | post_batch, rollback, same_value_rewire, cycle_batches, overflow, deep_chain, randomized | 67761 |
| learned-06 | 9/11 | rollback, randomized | 50376 |

均值为 8/11，完整通过为 1/3；对应无文档对照均值 10/11，完整通过也为 1/3。
这组数据没有证明文档提升，不能只拿 learned-04 的 11/11 下结论。
另外，learned-04 也仍存在全量候选结构和缓存复制，原评分器的复杂度覆盖限制同样适用。

## 执行阶段提供经验：单独对照

从实际 Interaction 交接记录发现，它会把经验中的建议写成实现约束，可能过早限制 Leader。
实现已调整为 Interaction 只整理用户需求与业务事实，文档目录在普通工作/Leader 执行阶段提供；
Interaction 提示词明确禁止将经验、临时方案或假设升级为用户要求。此前所有结果保留。

为了区分该实现变化与文档本身，另起 `/tmp/rsi-experience-scope-20261015`：
使用相同固定文档 `25b2af...`、同一任务、模型、预算与评分器，原计划重新运行三次无文档和三次有文档。
实际只运行了已启动的一对，协议修订时间与理由见下一节。这一对中没有更新文档。

这一对短预算校准最终结果：无文档 8/11，有文档 11/11；后者通过了前者失败的整批变更、
相同值重连与随机更新检查。这是一次正向观察，样本只有一对；不能据此推翻前面固定版本验证的负面结果，
也不能把它描述为已证明稳定或统计显著的收益。

| 尝试 | 检查组通过 | 失败组 | Leader tokens |
| --- | --- | --- | ---: |
| scope-baseline-01 | 8/11 | post_batch, same_value_rewire, randomized | 28839 |
| scope-docs-01 | 11/11 | — | 48307 |

两次状态均为 budget_limited。

### 在查看新评分前修订完整任务协议

scope-baseline-01 与 scope-docs-01 运行期间（两者均未产生 result.json），复查发现所有早期尝试都在
300 活跃秒被截断，很多未进入验证阶段。短预算曲线只能反映中间成果，无法充分检验“任务完成后复盘”。
因此保留已启动的一对 scope 校准，撤销尚未启动的另外两对；不把这次修改说成预先固定不变的方案。
这项修改基于所有尝试的预算状态和执行轨迹，在查看 scope 对照分数之前决定。

较大预算实验另起 `/tmp/rsi-experience-complete-20261015`，每轮预算固定为 250,000 tokens / 1,200 活跃秒，
外层另留 300 秒给 Interaction。继续使用完全相同的规格与评分器，普通完成/阻塞/失败/预算限制都如实记录。
先完成一次无文档任务并在独立会话中提炼通用经验，再以全新工作区/原生会话复做；另保留无文档对照。
不与 300 秒尝试直接比较质量或用量，不将更大预算本身产生的提升归因于文档。

该组安排两次无文档尝试（complete-baseline-01、02），第一次停止后生成经验，
随后两次新会话参考逐轮更新的文档。预算、任务、模型、评分方法相同；不复用上一轮代码或对话。
记录终态、检查通过情况、tokens 和活跃时间；若某轮仍耗尽预算，明确计为受限尝试。

## 较大预算结果

| 尝试 | 检查组通过 | 失败组 | Leader tokens | 活跃秒 | 总耗时秒 |
| --- | --- | --- | ---: | ---: | ---: |
| complete-baseline-01 | 10/11 | rollback | 263122 | 848.6 | 934.1 |
| complete-baseline-02 | 10/11 | post_batch | 264318 | 479.0 | 550.3 |
| complete-docs-01 | 11/11 | — | 273837 | 567.4 | 621.5 |
| complete-docs-02 | 11/11 | — | 268519 | 966.8 | 1070.2 |

四次状态均为 **budget_limited**：达到 token 预算，未触及 1,200 活跃秒限制。
无文档两次分别在未知引用的异常处理、同批删除/改写的最终状态检查上失败；
参考经验的两次没有触发这些评分失败。组均值从 10/11 到 11/11，全部检查通过从 0/2 到 2/2。
样本太少，不能据此推断稳定成功率；也没有观察到可宣称的 token 或时间节省。
前述评分器对渐进复杂度覆盖不足的限制仍适用。

经验由独立会话逐次生成，没有人工改写正文，主题为 `transactional-reactive-graph-validation`：

| 来源尝试 | 发布版本 SHA-256 | 下一次使用者 |
| --- | --- | --- |
| complete-baseline-01 | `1b939c25ddfd9bf3dd562d65e48e65d0e5cc2c90b96a9c0a979757ac01d791c2` | complete-docs-01 |
| complete-docs-01 | `5bee16b649bfaccbbd176f2d41b28445743a5e9a56882cf3403bbe3274116e54` | complete-docs-02 |
| complete-docs-02 | `4ff0e2f48e2f0ab49548e5e42240c951766cb69ede764af0b87eedde7e307b9f` | 尚未验证后续效果 |

文档保留的是批后校验、原子提交、失败后状态检查、独立参考模型、深链和局部更新的验证方法；
审查这三个版本未发现题目实现代码、特定测试输入或任务专用常量。它们是面向一类问题的经验，
并非已经验证可迁移到任意领域的通用技能。

轨迹还暴露了另一个问题：已有可运行结果时，Leader 仍可能继续补充测试或整理，直到预算耗尽。
完成上述评分后，产品提示词增加了“满足目标并完成必要检查即结束，不为耗尽预算而扩展工作”。
这项后续调整通过现有行为测试，但没有重新进行真实 API 对照，不能将本表收益归因于该调整。

## 自动总结的独立真实 API 验证

另用一个较小任务验证完整终态：先在普通对话确定业务字段，再 `/rsi` 生成并验证 JSON 文件。
Leader 正常完成目标，随后宿主自动启动独立只读整理会话，成功发布经验文档。

- run：`run-8b890e1df791`，任务与总结状态均为 `completed`。
- 普通对话：`01a14019-7788-7680-8b78-c1d91327645c`。
- Leader：`01a1401a-1b67-7863-86b4-0eaec11ff94e`。
- 整理者：`01a1401b-bda0-77c1-8bcd-798a4202de10`。
- 文档：`validated-json-artifact-workflow/e0290e3a9d6a357ea66c4a705f08755d94c47bbe39baabb8997917e0fd1486bc.md`。

检查确认文件内容符合之前讨论的要求，三个原生会话 ID 不同，总结未保留具体业务字段名。
这证明自动沉淀链路可运行，不作为复杂任务性能提升的样本。

## 复现与原始记录

```bash
# 使用新的实验目录和标签，避免复用历史任务。
uv run --locked python tests/experience_e2e/run.py baseline-01 --root /tmp/rsi-new-study --seconds 1200 --tokens 250000 --learn
uv run --locked python tests/experience_e2e/run.py docs-01 --root /tmp/rsi-new-study --seconds 1200 --tokens 250000 --use-docs --learn
uv run --locked python tests/experience_e2e/run.py baseline-02 --root /tmp/rsi-new-study --seconds 1200 --tokens 250000
uv run --locked python tests/experience_e2e/run.py docs-02 --root /tmp/rsi-new-study --seconds 1200 --tokens 250000 --use-docs --learn
```

这是付费真实 API 实验，使用项目 `.env`；标签必须新建，避免覆盖旧工作区。
当前代码包含上文说明的后续提示词调整，因此以上用于复做方法，不保证逐字复现旧运行。
2026-10-16 起新会话默认全目录访问，总结继承会话权限；本页实验发生在此前，使用当时的只读总结配置。
每轮目录包含 workspace、result.json 和成功总结后的 learned.json；data 保存原生会话、
文档不可变版本及 session.json。评分器在 `tests/experience_e2e/grade.py`，没有复制到候选工作区。
报告不保存 API key。没有开启原生跨会话 memory，也没有将实验经验自动加入用户日常资料库。

原始产物已原样归档到本地 `.rsi/experiments/2026-10-15/`，该目录不参与 Git 提交：

| 原始目录 | 归档子目录 |
| --- | --- |
| `/tmp/rsi-experience-study-20261015` | `exploration` |
| `/tmp/rsi-experience-validation-20261015` | `frozen-validation` |
| `/tmp/rsi-experience-scope-20261015` | `execution-scope` |
| `/tmp/rsi-experience-complete-20261015` | `larger-budget` |
| `/tmp/rsi-auto-experience-smoke-20261015` | `automatic-learning` |

记录里的绝对路径和来源标识未被重写；`/tmp` 在 macOS 上可能显示为 `/private/tmp`。
临时目录被清理后，可按归档 README 的前缀对应关系查阅，归档本身不是自动迁移后的可恢复会话。
