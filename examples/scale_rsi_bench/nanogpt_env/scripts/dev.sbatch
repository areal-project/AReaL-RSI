#!/usr/bin/env bash
#SBATCH --job-name=wht-rsi-dev
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=nanogpt_env/logs/dev-%j.log
set -euo pipefail
# Submit from scale_rsi_bench/: sbatch nanogpt_env/scripts/dev.sbatch
ROOT=${SLURM_SUBMIT_DIR:?}/nanogpt_env
srun --mpi=pmi2 bash "$ROOT/scripts/container.sh" bash /workspace/dev_eval.sh "${1:-/workspace/submission/selection.json}"
