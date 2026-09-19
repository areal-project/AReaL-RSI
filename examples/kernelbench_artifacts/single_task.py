"""Prepare one task for Interaction, including reference calibration before research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from demo import HERE, prepare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run-id", required=True, help="当前工作台研究 ID")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--environment", type=Path, default=HERE / "et15.json")
    parser.add_argument("--max-agents", type=int, default=4, help="固定独立研究员数量")
    parser.add_argument("--time-limit", type=int, default=3600, help="时间上限（秒）")
    parser.add_argument(
        "--time-scope",
        choices=("exploration",),
        default="exploration",
        help="只限制探索，最终复测另计",
    )
    parser.add_argument("--name", help="工作台中的研究名称")
    parser.add_argument(
        "--submissions", choices=("unlimited",), default="unlimited", help="探索提交不限次数"
    )
    parser.add_argument(
        "--web-research",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="新研究默认允许联网调研；--no-web-research 用于不联网的 baseline 对照",
    )
    args = parser.parse_args()
    prepare(
        args.data_root.resolve(),
        args.environment.resolve(),
        args.run_id,
        task=args.task,
        max_agents=args.max_agents,
        max_wall_seconds=args.time_limit,
        check_credentials=False,
        web_research=args.web_research,
        exploration_submissions=None,
        name=args.name,
        time_limit_scope=args.time_scope,
    )
    print(
        json.dumps(
            {
                "task": args.task,
                "config": str(
                    args.data_root.resolve() / "runs" / args.run_id / "setup/research.json"
                ),
                "next": "用 load_execution_config 导入，由 Interaction 确认需求、发起研究。",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
