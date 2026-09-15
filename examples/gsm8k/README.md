# GSM8K 数学研究

使用固定修订版的官方 GSM8K train，将题面按 SHA-256 排序，每十题留一题作 VALIDATE。官方 test 不参与研究，结果不是官方 test 成绩。私有答案由宿主 Verifier 读取。

```bash
uv run --locked python -m areal_rsi.modules.gsm8k --directory .rsi/datasets/gsm8k
uv run --env-file .env --locked rsi prepare --case gsm8k \
  --dataset .rsi/datasets/gsm8k --limit 2 \
  --output .rsi/configs/gsm8k/research.json
uv run --env-file .env --locked rsi tui --config .rsi/configs/gsm8k/research.json
```

TUI 输入 `/start`。`--limit 2` 表示分别使用 2 道训练题和 2 道留出验证题；默认真实小规模验收，不保证成绩提升。扩大题数时生成新配置，不覆盖旧研究条件。

也可使用 `rsi research --config ...` 直接运行，或 `rsi workbench --config ...` 在 Web 启动；所有命令前加 `uv run --env-file .env --locked`。研究员仅修改 `prompt.md`；Task Agent 使用 `RSI_TARGET_*` 连接解题。研究角色使用配置中的研究模型连接。

研究过程包含 baseline、训练输出分析、候选修改、search 和 final 复测。search/final 使用同一留出题池，只有训练作业公开原始输出。评分沿用最后一行 `#### NUMBER` 的数值匹配规则。

恢复同一研究：`rsi research --resume <run_id>`，或 `rsi tui --resume <run_id>`。TUI 中暂停后的研究输入 `/resume`。保持相同 `RSI_DATA_ROOT`。

最小原创算术示例用 `--case arithmetic`，无需下载数据，详见[算术示例](../local/README.md)。
