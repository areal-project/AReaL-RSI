"""Stage upstream task files without overwriting existing experiment work."""
import json
from pathlib import Path
import shutil
from env_paths import runtime_root
ROOT = runtime_root(__file__)
cfg = json.loads((ROOT / "config.json").read_text())
env = ROOT / "source/tasks" / cfg["task"] / "environment"
work = ROOT / "workspace"
def copy_missing(src, dst):
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            copy_missing(item, dst / item.name)
    elif not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
copy_missing(env / "workspace", work)
copy_missing(env.parent / "instruction.md", work / "instruction.md")
for name in ("assets", "baseline"):
    if (env / name).exists():
        copy_missing(env / name, ROOT / "assets" if name == "assets" else work / name)
for name in ("submission", "runs", "eval_results", "dev_runs"):
    (work / name).mkdir(exist_ok=True)
if cfg["task"] == "on-policy-self-distillation":
    copy_missing(env / "paper.pdf", work / "paper.pdf")
    recipe = work / "submission/recipe.env"
    if not recipe.exists():
        recipe.write_text("# Empty recipe uses the official OPSD baseline hyperparameters.\n")
if cfg["task"] == "jailbreak-robustness":
    copy_missing(env / "eval_src", ROOT / "eval_src")
    google = ROOT / "vendor/google-research/instruction_following_eval"
    if google.exists():
        (ROOT / "eval_src/instruction_following_eval").mkdir(parents=True, exist_ok=True)
        for file in google.glob("*.py"):
            copy_missing(file, ROOT / "eval_src/instruction_following_eval" / file.name)
    copy_missing(ROOT / "eval_src", work / "eval")
    copy_missing(env / "workspace/README_data.md", work / "data/README.md")
print("Task workspace staged:", work)
