"""Small reproducible defensive SFT recipe for checkpoint-to-eval integration testing.

Uses provided refusal/benign targets only. This is not a tuned benchmark method.
"""
import argparse
import json
import math
from pathlib import Path
import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, DataCollatorForSeq2Seq, Trainer, TrainingArguments, set_seed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    set_seed(42)
    tok = AutoTokenizer.from_pretrained("/opt/models/Qwen3-8B", local_files_only=True)
    rows = []
    for filename, safety in (("refusal_safety.jsonl", True), ("benign_retain.jsonl", False)):
        count = 0
        for line in (Path("/workspace/data") / filename).open():
            row = json.loads(line)
            prompt = row["prompt"] if safety else row["instruction"] + "\n" + row.get("input", "")
            answer = row.get("refusal", "") if safety else row.get("output", "")
            if not answer.strip():
                continue
            text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False,
                                           add_generation_prompt=True, enable_thinking=False)
            prefix = tok.encode(text, add_special_tokens=False)[-256:]
            target = tok.encode(answer, add_special_tokens=False)[:255] + [tok.eos_token_id]
            rows.append({"input_ids": prefix + target, "attention_mask": [1] * (len(prefix) + len(target)),
                         "labels": [-100] * len(prefix) + target})
            count += 1
            if count == 64:
                break
    assert len(rows) == 128
    model = AutoModelForCausalLM.from_pretrained("/opt/models/Qwen3-8B", local_files_only=True,
        torch_dtype=torch.bfloat16, attn_implementation="sdpa")
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"))
    model.config.use_cache = False
    trainer = Trainer(model=model, train_dataset=Dataset.from_list(rows).shuffle(seed=42),
        data_collator=DataCollatorForSeq2Seq(tok, label_pad_token_id=-100),
        args=TrainingArguments(output_dir=str(args.out / "trainer"), max_steps=16,
            per_device_train_batch_size=2, gradient_accumulation_steps=4,
            learning_rate=1e-4, bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False}, logging_steps=1,
            save_strategy="no", report_to="none", seed=42, optim="adamw_torch"))
    result = trainer.train()
    assert trainer.state.global_step == 16 and math.isfinite(result.training_loss)
    checkpoint = args.out / "checkpoint"
    model.save_pretrained(checkpoint)
    (args.out / "training-result.json").write_text(json.dumps({"steps": 16, "examples": 128,
        "loss": result.training_loss, "lora_rank": 8, "seed": 42, "official_score": False}, indent=2))
    print(f"JAILBREAK TRAINING PASS: {checkpoint}", flush=True)

if __name__ == "__main__":
    main()
