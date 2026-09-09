# Research 工作台验收记录

## DSH 基础工具与原会话兼容（2026-09-09）

Interaction、RSI 和 Research 共有 DSH 终端、文件读写/编辑、文件搜索及联网工具。全量回归 **414 项测试、62 个 subtests** 通过，包含 10 项新增测试；Ruff、mypy 和 Node 语法检查通过，wheel 包含固定的 Node 依赖清单、lockfile、worker 及 schemas。

真实 DSH 测试覆盖文件操作与搜索、默认工作区区分、读取后修改保护、超时/取消后的进程清理、已完成调用的重放，以及结果不明时拒绝再次执行。角色权限测试确认只有 RSI 可以创建研究员，只有 Research 提交报告；新增终端不改变这些业务接口。

在正在使用的 Interaction 对话的**独立副本**中，使用真实 `gpt-6-astra / xhigh` 完成兼容性检查：原 session ID 不变，42 条历史记录完整保留，新增 3 次模型调用；Agent 实际调用 DSH `read` 阅读用户指定的 KernelBench 指南，再调用 DSH `bash` 经 SSH 读取远端主机名，均成功。完成后等待，重新附着 worker 不增加模型调用。没有启动 KernelBench 实验或进行 GPU、正确性与性能验证。

验收驱动最初使用了不存在的空闲查询方法，在模型完成后报错；随后直接核对持久记录，并独立检查等待状态，没有重复运行模型。结果及调用记录在本地 `.rsi/dsh-acceptance/copied-chat/acceptance.json`，直接工具检查在 `.rsi/dsh-acceptance/`。

正式服务更新前已备份 run 数据并核对文件摘要。重启时原消息、会话、需求、待回答问题及所有已有 run 文件与备份一致；随后恢复空闲 worker，正式对话仍为 planned，模型调用保持 15 次，没有混入验收消息。部署核对记录为 `.rsi/dsh-acceptance/deployment.json`。

## RSI 直接向用户提问（2026-09-09）

RSI / Interaction 的提问直接展示给用户，回答按问题 ID 回到原提问者的会话。全量回归为 404 项测试和 62 个 subtests；Ruff、mypy 与前端语法检查通过。新增覆盖直接投递、重复提交、旧记录恢复、多问题并存、暂停期间归档、重启接续和等待期间无模型调用。

真实模型验收使用 `gpt-6-astra / xhigh`，run 为 `research_79303a1e44eb4db2`，共 11 次调用、49,005 tokens。真实 CPU 探针首次退出码为 1，返回受控的 401 访问拒绝；RSI 读取错误后直接提问，说明事实和用户需要处理的事项。等待期间调用数保持 6；外部修复后，回复直接回到同一个 RSI 会话，第二次探针退出码为 0，RSI 核对日志后完成。未唤醒 Interaction，也未生成正式分数。这验证了访问异常的处理流程，不代表访问了真实 GPU 或目标模型。

证据保存在本地 `.rsi/question-acceptance/`：`real-before.json`、`real-after.json`、`real-checked.json` 和原始 run。验收脚本最初误按作业列表末项判断先后；按 `started_at` 核对归档后确认作业顺序为失败→成功，脚本断言也已修正，未因此重复调用模型。

独立 Chrome 桌面及 390px 手机宽度验收通过：展示两个提问者、直接回复 RSI、保留其他问题、轮询保留草稿与接收者、刷新恢复、其他窗口已回答时不改投草稿，以及模型服务报错后的原会话重试。使用确定性模型响应测试界面；浏览器截图和 `browser-result.json` 同样保存在上述目录。

## 真实模型演练的行为观察

本节只读核对 `.rsi/acceptance-v2/runs/` 中的状态、公开报告、工具动作、事件和会话目录；未新增模型调用，也未读取原始隐藏推理。RSI/Research 使用真实 Astra，GPU、训练、Task Agent 和评分均为模拟，下面的分数只能证明流程连通，不能说明实际性能提升。

### KernelBench：已完成

Run：`research_c5519aeeb3d8401d`。43 次模型调用、367,403 tokens、5 次模拟验证。RSI 读取种子和开发轨迹，建立起点 search 分数 0.30；同一个 Research Agent 连续完成两次资料审查，RSI 根据报告亲自修改 skill。较宽泛的方案得到模拟 search/final 0.50，缩窄到边界访存与 correctness-before-timing 后得到模拟 search/final 0.70。

- 分工与内容：研究任务明确要求只读审查、不得编辑；Research Agent 遵守约束。报告写清了越界输出与过早计时两类失败、具体建议文字及成本取舍，明确说明没有执行 GPU 实验。
- 连续会话与回复：两个节点共用 `research_d44b3309348055ffc30aa138` 的一个会话目录。首份 report 事件 98 至 RSI 新 assignment 事件 105 之间没有该研究者的模型调用；第二份 report 后也没有继续调用。新 assignment 显式引用上次 report ID。
- 最终归属：选定候选 `candidate_139c1ed77ecc6ee9dadd0b41` 的 search 与 final 都是模拟 0.70，run 成功状态与最终候选一致。没有把较早候选的 0.50 final 当作最终产物的成绩。
- 导出：已检查该 run 的 `exports/export_039cdbec3c2d48e8.zip`，其中含 harness 文件与 `research-result.json`；导出候选 ID 与 final 记录相同，标记为模拟 0.70，没有把模拟成绩当作真实性能。
- 经验管理：RSI 保存了比较证据并记录模拟限制，但本次没有 `share_evidence` 事件。单研究方向的归档已覆盖，跨方向公共经验消费由 Kaggle 演练补充观察。

### CUHK-X：V2 因预算暂停

Run：`research_e36b7ae755c74ff7`。V2 最终观察结果为 `budget_paused`：122 次模型调用、2,704,260 tokens，search 与 final 均得到模拟 0.30，未达到 0.65 的目标，因此不能记为成功完成。没有评测过的 baseline，种子只作为源码起点。运行档案保持原样，后续改善用 V3 重新演练。

- 分工：RSI 先搜索并发起两个方向，分别研究紧凑时序结构和训练/数据协议；自己并行实现初稿。训练方向提交报告后，RSI 共享其经验，显式回复并要求基于已有候选做有限的数据处理修改。研究者实际调用了 `checkout_candidate` 后继续编辑。
- 真实调研：工具归档包含 CVF 的 TSM 页面、作者公开 `temporal_shift.py`、TSN 作者全文和 Crossref 元数据等实际响应。遇到搜索验证、403、404 和空页面后能够切换来源，报告区分全文、元数据和推断；没有把 RGB/预训练论文成绩声称为本任务的效果。
- 报告质量：首轮两份报告指出种子时间均值不感知顺序、TSM 默认参数差异、不能根据单模态路径猜测同步配对，以及 augmentation 建议尚未验证。这些信息足以支持 RSI 下达具体下一步。
- 连续性与等待：两个方向各自保持一个会话目录；训练方向出现一次 `session_compacted` 事件。其 report 事件 368 至下一 assignment 385 之间没有模型调用；架构方向 report 403 后没有擅自继续。
- 执行诚实性：唯一训练 job 只产生模拟标记，没有 checkpoint。RSI 已单独归档这一限制，明确说实现的是待验证源码，不能声称得到训练权重或比赛准确率。后续 search/final 的模拟 0.30 只验证了评分接线，没有建立真实训练产物或性能结论。

### CUHK-X：V3 未达标，交互收尾完成

Run：`research_e1f522da6f334c7d`。自主研究阶段运行 122 次模型调用、2,143,122 tokens、3 次模拟验证；两个 Research Agents 共完成 4 个节点。首个已验证候选 search 为 0.30，后续候选 `candidate_ead7ce5a830ba47a6213599c` 的 search/final 均为 0.50，低于 0.65 目标。RSI 停止研究方向、保留结果并请求用户决定，CLI 以 waiting_for_user / exit 3 保存现场，没有宣称成功。

- 分工：实际文件写入、checkout 和候选冻结由 Research Agent 承担，RSI 没有执行 write_file；另一个方向提供真实论文证据。实现方向连续执行初稿、静态审查和有界修改。4 个报告均明确区分源码检查与未执行的 self-check/训练。
- 等待与共享：报告事件 295→新任务 310、报告 390→新任务 433 之间，该研究者模型调用均为 0；另外两份 report 后也没有自行继续。两条方向分别保持一个 session，RSI 和实现方向各发生一次 compact；共发布 3 次共享 evidence。
- 研究判断：RSI 明确不能由模拟 0.30 推断架构失败，没有为了达到 fixture 分数无限重验。此次分工改善可以确认；仅凭这两次运行不能证明整体 token 效率或研究成功率改善。约 214 万 tokens 的自主研究开销仍然偏高。
- 交互收尾：验收脚本通过真实网页提交一条**明确标记为脚本模拟的用户回复**，要求保留未达标状态结束，不能降低 target、追加研究或验证。Interaction 转交后 RSI 实际调用 stop_run；最终为 stopped，仍保留 0.50 和原来的 0.65 目标。此交互追加 6 次真实模型调用，合计 **128 次、2,224,222 tokens**，verify 次数仍为 3。它验证的是用户决定后的停止链路，不能称为自主达标。
- 导出：`exports/export_30d3c91c394e4eed.zip` 的候选 ID 与 final 模拟 0.50 记录一致，4 个 Python 文件通过语法解析。没有实际训练 checkpoint，也没有运行生成的 selfcheck.py；源码导出不等于模型性能或可运行性已经验证。

### 暴露的问题与轻量改进

1. 两例都曾把文件路径或网页 URL 放进 `references`，被工具拒绝后自修复。已补 references schema/prompt 的 `run://` 要求，并让 evidence 工具直接返回 URI 和 ID。
2. RSI 承担了较多代码修改，研究者初期主要做文献/文字审查。已将默认分工写入 prompt：实现、实验和迭代执行优先委托 Research Agent，RSI 保留调研、必要整合与小改的自由，没有固定研究流程。
3. 报告仍有压缩空间：Kernel 两份主体约 3,484/1,622 字符，Kaggle 首轮两份约 4,323/1,252 字符。prompt 现明确整份 report 约 400–800 中文字或 250–400 英文词，保留结论、关键证据、实际改动及下一步；长题录、代码和推导放到链接的 materials/evidence。
4. Kaggle 在首轮验证前已使用约 169 万 tokens，最终约 270 万并预算暂停；两个方向有重复 TSN 获取，公共 evidence summary 也较长。prompt 现要求先检查已有证据，只围绕影响当前决策的缺口补查，完成 assignment criteria 后上报。`get_state` 的每条 evidence summary 只预览前 1,000 字符，并返回 `truncated`、完整长度和可读 URI；`read_record` 仍保留全文。测试核对了预览截取不会修改归档内容。

5. V3 请求收尾时曾提出“豁免 target”的选项，暴露了未达标结束工具与表述的缺口。已增加 RSI 专属 stop_run，保留未达标事实；Interaction prompt 明确不能通过降低或豁免目标宣称成功。自动化测试和上述真实模型脚本收尾验证了新停止路径。

两例验证了研究者等待屏障、同方向连续会话、公共证据与事实/模拟结果区分。KernelBench 达到模拟目标；CUHK-X V2 因预算暂停，V3 经用户交互以未达标状态结束。这些是不同的实际结果，不能合并成“两例都达标”。

## 实际 CPU 训练与系统桥接

记录：`.rsi/example-smokes/cuhkx-bridge-20260909/summary.json`，run 为 `research_7636e53fb0f446bb`，验收状态 `passed`。这是**实际 CPU 运算、合成数据**的桥接验收：`simulated=false`、`dataset_kind=synthetic`、GPU jobs=0、模型 API 调用=0，也没有构建模型会话 runtime。

- 通过研究系统的作业/候选接口完成训练产物保存、匿名预测、可信评分及 ZIP 导出；没有要求先产生已评测 baseline。
- search 与 final 使用不同的 manifest 和 protocol ID，核对为同一候选、同一 checkpoint SHA256。两次实际计算的准确率均为 1.0，但只针对这组简单合成数据，不能解释为 Kaggle 成绩。
- `verified-candidate.zip` 已产生并记录 SHA256；桥接检查保留了研究者的 session 身份。这一项没有真实 LLM 调用，因此不替代前面的真实 agent 行为演练。

该验收覆盖真实训练/权重/预测/评分接线，未执行 CUDA 或 Docker，也没有验证真实比赛数据上的训练收益。

## 网页与 Interaction

记录目录：`.rsi/workbench-acceptance/`。两类验收分别记录，避免将 fixture 当作真实模型行为。

- `acceptance.json` 与截图：本地浏览器 fixture 覆盖多方向图、父子与合并边、节点 report/evidence、约束展示、新建和确认后启动、暂停/恢复/停止、产物下载、增量刷新、断网重连、草稿与节点选择保持。390px 移动视图检查无页面横向溢出，并保留图内滚动位置；JavaScript errors 为空。fixture 的分数与产物均明确为模拟。
- `actual-interaction.json`、`actual-interaction.png`：通过真实模型网页对话提交“只优化 skills、最多一个 Research Agent、优先分析错误重试、暂不开始”。Interaction 区分并复述硬限制与非强制建议，回复已同步 RSI；run 仍待确认，研究没有启动，JavaScript errors 为空。此项覆盖真实需求确认的一次往返，不代表所有复杂交互场景均已验收。
- `bootstrap-acceptance.json`：有/无 operator 配置、模拟开关可用/不可用的三组创建入口配置均通过，避免没有真实执行配置却展示可直接开始真实研究的入口。

- `final-kernelbench.json`、`actual-cuhkx-browser.json`：最终代码读取真实运行档案，分别呈现两节点和两方向四节点的研究图，桌面/移动端无 JavaScript 错误。
- `cuhkx-scripted-close.json`、`cuhkx-stopped.png`：前述脚本用户回复经真实模型转交后，网页显示已停止且分数仍为 0.50。
- `node-detail-acceptance.json`：实际长任务暴露出标题挤占报告的问题，已改为普通正文与可展开全文；fixture 和真实 KernelBench 只读检查通过，展开状态保持，390px 无横向溢出。修后截图为 `final-kernelbench-node-readable.png`。

已查看桌面实际对话、长任务详情和移动总览截图，内容与模拟状态可辨识、布局可读。当前对话采用纯文本展示，截图中模型输出的 Markdown 标记仍可见；富文本呈现尚可改善。

## 自动化检查与剩余边界

上述演练完成时，全套执行收集 394 项，结果为 **393 passed、1 failed**。唯一失败项是已有 macOS 集成测试 `tests/test_runtime_isolation.py::IsolationTest::test_shell_cannot_read_a_sibling_answer_file`：前面的公开文件读取、私有文件拒绝等检查通过；末尾通过 `sandbox-exec` 启动 `.venv/bin/python` 检查 PTY 时出现 `Operation not permitted`，退出码 71。不能将这一结果描述为整套测试全绿，或声称该平台完整隔离集成验收通过。

Ruff 对 `src/tests/scripts` 的检查、mypy 对 113 个文件的检查以及 `git diff --check` 均通过。最后的长任务排版修正另通过 7 项工作台测试、JavaScript 语法检查和实际浏览器验证。前一轮另外记录了 62 个 subtests 通过；最终这次使用双 quiet 输出，没有单列 subtest 计数。

尚未完成的实际环境验收包括：真实 CUDA 性能/正确性、真实 Docker 候选隔离、SSH GPU 作业取消与重连、真实比赛数据与模型训练效果。KernelBench 上游在同一 Python 进程加载候选与 reference，当前 wrapper 不能宣称已隔离恶意 kernel 对评测机制的攻击。V3 已完成未达标停止与导出验收，仍不能算作达成研究目标。

## 中文 Prompt 调整

上述完整演练使用的是此前的英文角色 prompt。根据用户的中文偏好，随后将 Interaction、RSI、Research 角色指令、compact/恢复/预算提示、系统消息和两个模拟示例任务说明改为中文，默认用简体中文生成计划、任务、报告和证据摘要。历史档案保持原样。

本次调整通过会话、调度、协作、应用和 CLI 的 64 项回归测试及 2 个 subtests，Ruff 和 mypy 通过；没有重新运行完整的真实模型演练，不能将此前的行为结论视为中文版 prompt 的新增实测。

## 正式对话入口与实际执行验收（2026-09-09）

这轮验收使用中文指令与真实 `gpt-6-astra / xhigh`，测试数据保存在独立的 `.rsi/formal-acceptance/`，不加载到正式工作台。

| 检查 | 实际结果 |
| --- | --- |
| KernelBench 需求对话，经真实浏览器和 Web API | `research_51ae610d99f8404b`；4 次模型调用、12,793 tokens。用户只说明目标、只改 skills、一个研究员，并表示稍后提供 GPU 与题池；Interaction 追问缺少条件后等待，没有启动 RSI 或作业，也不再询问已配置的研究模型。 |
| 通用 CPU 研究，经对话准备与自然语言开始指令 | `research_f5cba6d3c29b4b85`；36 次模型调用、287,393 tokens。1 个 Research Agent、1 个节点、1 次真实本机作业，最终 `succeeded`。没有 baseline、Task Agent、正式 verifier 或数值 target。 |
| CPU 实验交付 | 比较列表查找与 set 辅助的稳定去重：先核对输出一致，再各计时三遍。已读取最终候选的实际内容，包含 `experiment.py`、`results/results.json`、`results/report.md` 和脚本副本。最终候选为 `candidate_fc7776660460fd4dc16556fb`，不是最后工作区的临时状态。 |
| 浏览器 | 1440×1000 桌面与 390×844 手机；覆盖无模板的新对话、首条消息、真实 Interaction 回复、开始前拦截、刷新保留草稿、动态方向、节点详情和无分数研究。没有 JS 错误或页面横向溢出；图高度按研究方向数量调整。 |
| 自动化检查 | 最终完整回归 **400 passed**；Ruff 通过，mypy 检查 116 个源码与测试文件通过，前端 JS 语法检查通过。 |

首次 CPU 接入测试保留为 `research_55180493e26b4758`，没有改写成成功：Interaction 将输出参数写成未声明的 `{output_dir}`，并假定输出子目录为 `results`。实际作业运行了，但输出工具不能收集所要求的文件，研究最终 `stopped`。据此补上未声明占位符检查、系统输出参数示例和公开输出目录说明；第二次测试中，Interaction 根据工具错误修正配置，再进入上述成功链路。无验证任务现在可以设置零次验证预算，不再为无关字段额外提问。

此前记录的 macOS 沙箱失败也已修复。uv 的 Python 可执行文件经过一个中间目录符号链接，原 profile 只包含最终安装路径，缺少该别名的元数据权限。修复只允许遍历运行时链接的元数据，没有开放其父目录中的文件内容；回归同时验证 Python 可运行、私有兄弟文件仍不可读取，共享运行时仍不可写。

具体证据：`pytest-final.txt`、`intake-result.json`、`browser-intake.json`、`cpu-result-v2.json`、`cpu-verified-artifacts.json`、`browser-welcome.json`、`browser-live.json` 以及同目录截图。初次失败记录位于 `cpu-result.json`，完整 run 与会话在对应 `data / cpu-data / cpu-data-v2 / web-data-v2` 子目录中保留。

这次真实执行验证的是通用编排与本机 CPU 实验链路。KernelBench 的目标模型、用户 GPU、实际题池和性能提升仍需用户在正式对话中提供环境后运行；CUHK-X 的真实比赛训练与成绩也没有在这轮测试中执行。既有模拟成绩仍只是过去的流程验收数据。
