from areal_rsi.config import RSIRunConfig


def test_rsi_config_loads_yaml_and_applies_overrides(tmp_path):
    config_path = tmp_path / "rsi.yaml"
    config_path.write_text(
        """
run:
  initial_ckpt: ckpt_000
env:
  task_set: swe_verified
""",
        encoding="utf-8",
    )

    config = RSIRunConfig.load(config_path).with_overrides(
        {
            "run.max_iterations": 2,
            "run.method": "sft",
            "env.harness_config.harness": "custom_harness",
        }
    )

    assert config.source_path == config_path.resolve()
    assert config.source_hash.startswith("sha256:")
    assert config.data["run"]["max_iterations"] == 2
    assert config.data["env"]["harness_config"] == {"harness": "custom_harness"}
    assert config.to_run_loop_kwargs() == {
        "initial_ckpt": "ckpt_000",
        "task_set": "swe_verified",
        "harness_config": {"harness": "custom_harness"},
        "method": "sft",
        "max_iterations": 2,
        "stop_criteria": {},
        "train_config": {"epochs": 1},
        "traj_filter": None,
        "eval_gate": {"holdout_task_set": "swe_verified_holdout"},
    }
