"""Fail before training if an allocated GPU is unexpectedly occupied."""
import json
import torch
assert torch.cuda.is_available()
devices = []
for index in range(torch.cuda.device_count()):
    free, total = torch.cuda.mem_get_info(index)
    devices.append({"index": index, "free_gib": round(free / 2**30, 2), "total_gib": round(total / 2**30, 2)})
    assert free > 0.92 * total, f"Allocated GPU {index} is occupied: {devices[-1]}"
print(json.dumps(devices), flush=True)
