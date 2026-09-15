"""Local environment checks only; no judge API calls or benchmark scoring."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(os.environ["RSI_ROOT"])
cfg = json.loads((ROOT / "config.json").read_text())
model_path = "/opt/models/" + cfg["model"].split("/")[-1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["imports", "lora", "vllm"])
    args = parser.parse_args()
    if not args.phase:
        (ROOT / "logs/SMOKE_PASS.json").unlink(missing_ok=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/check_assets.py")], check=True)
        phases = ["imports", "vllm"] if cfg["task"] == "agent-swarm-optimization" else ["imports", "lora", "vllm"]
        for phase in phases:
            subprocess.run([sys.executable, "-u", __file__, "--phase", phase], check=True)
        (ROOT / "logs/SMOKE_PASS.json").write_text(json.dumps({"task":cfg["task"],"passed":True,
            "checks":phases,"official_score":False,"completed_at":datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
        print("ENVIRONMENT SMOKE PASS")
        return
    import torch
    assert torch.cuda.is_available(), "CUDA is not available"
    if args.phase == "imports":
        import vllm
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        assert tokenizer.encode("Hello")
        packages = ["torch", "vllm", "transformers", "numpy"]
        if cfg["task"] != "agent-swarm-optimization":
            import peft, trl, datasets
            packages += ["peft", "trl", "datasets", "accelerate"]
        if cfg["task"] == "on-policy-self-distillation":
            import deepspeed, flash_attn
            sys.path.insert(0, "/workspace/repo")
            import opsd_trainer
            from datasets import load_dataset
            assert len(load_dataset("siyanzhao/Openthoughts_math_30k_opsd", split="train")) > 0
            q = torch.randn(1, 16, 2, 64, device="cuda", dtype=torch.bfloat16)
            assert torch.isfinite(flash_attn.flash_attn_func(q, q, q, causal=True)).all()
            packages += ["deepspeed", "flash-attn"]
        elif cfg["task"] == "jailbreak-robustness":
            import nltk
            from ifeval_checker import follows_all_strict
            assert nltk.sent_tokenize("Hello. This is a test.")
            assert (ROOT / "workspace/data/refusal_safety.jsonl").stat().st_size > 0
            assert (ROOT / "workspace/data/benign_retain.jsonl").stat().st_size > 0
        else:
            sys.path.insert(0, "/workspace/methods/main")
            from swarm import Swarm
            graph = Swarm(edges=[(-1,0),(-1,1),(0,7),(1,7)])
            graph.validate()
        report = {"gpu":torch.cuda.get_device_name(0),"visible_gpus":torch.cuda.device_count(),
                  "versions":{p:importlib.metadata.version(p) for p in packages}}
        (ROOT / "logs/environment.json").write_text(json.dumps(report,indent=2)+"\n")
        print(report, flush=True)
    elif args.phase == "lora":
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, get_peft_model
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=torch.bfloat16,
            attn_implementation="flash_attention_2" if cfg["task"]=="on-policy-self-distillation" else "sdpa",
            local_files_only=True).cuda()
        model = get_peft_model(model, LoraConfig(r=4, lora_alpha=8, target_modules=["q_proj","v_proj"],task_type="CAUSAL_LM"))
        model.train()
        inputs = tokenizer("The sum of two and two is four.",return_tensors="pt").to("cuda")
        optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=1e-4)
        loss = model(**inputs, labels=inputs["input_ids"]).loss
        assert math.isfinite(loss.item())
        loss.backward()
        optimizer.step()
        assert any(p.detach().abs().sum().item()>0 for n,p in model.named_parameters() if "lora_B" in n)
        model.save_pretrained(ROOT / "logs/smoke-adapter")
        print(f"LORA TRAINING PASS loss={loss.item():.4f}", flush=True)
    else:
        from vllm import LLM, SamplingParams
        llm = LLM(model=model_path, tokenizer=model_path, dtype="bfloat16",
            tensor_parallel_size=1, max_model_len=2048, max_num_seqs=2,
            gpu_memory_utilization=0.30, enforce_eager=True, disable_log_stats=True)
        output = llm.generate(["The sum of two and two is"], SamplingParams(temperature=0,max_tokens=8))
        assert output and output[0].outputs[0].token_ids
        print("VLLM GENERATION PASS",flush=True)

if __name__ == "__main__":
    main()
