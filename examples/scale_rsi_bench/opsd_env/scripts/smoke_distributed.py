"""Four-GPU NCCL collective test; no benchmark training."""
import os
import torch
import torch.distributed as dist
rank = int(os.environ["LOCAL_RANK"])
torch.cuda.set_device(rank)
dist.init_process_group("nccl")
value = torch.tensor([rank + 1.0], device=f"cuda:{rank}")
dist.all_reduce(value)
assert value.item() == 10.0, value
dist.barrier()
if rank == 0:
    print("FOUR GPU NCCL PASS", flush=True)
dist.destroy_process_group()
