# Background execution

`JobManager` schedules configured argv commands and simulated jobs inside one
run. It performs no model inference and does not interpret stdout as metrics.

```python
from areal_rsi.execution import JobManager, JobSpec

manager = JobManager(run_root, gpu_ids=("0", "1"), max_jobs=2, on_event=consume)
job = manager.submit(
    JobSpec(command=("python", "train.py"), cwd=workspace, gpu_count=1, timeout_seconds=600),
    idempotency_key=tool_call_id,
)
manager.get(job.job_id)  # Current persisted state, no model polling.
manager.cancel(job.job_id)  # Cancels queued work or asks its supervisor to stop.
manager.close()  # Preserve active work for a subsequent manager.
```

Set `auto_monitor=False` to drive reconciliation explicitly with `poll()`. The
default monitor dispatches completion notifications and advances the queue on a
Python thread. `scheduling_enabled=False` leaves new/queued work stopped while
allowing already running supervisors to finish and results to be reconciled.
`close(preserve_running=False)` cancels queued and running jobs.

The run owns `jobs/job-*/job.json`, `launch.json`, `handle.json`, `stdout.log`,
`supervisor.log`, `cancel.json` and `result.json`. Records and launch environments
are private files. Do not expose `JobSpec.env` or raw launch records in the UI.
`jobs/events/` archives state notifications. A callback failure leaves its event
pending; retries and restart may deliver it again, so consumers deduplicate
`JobEvent.event_id`. A success exit status is process evidence, not a score.

A detached supervisor enforces timeout and cancellation while the application
is offline. It terminates the command's process group, including descendants that
remain in that group, then records its exit status. Recovery checks a PID's birth
identity and a unique launch token, never only a PID. Uncertain work becomes
`lost` and is never automatically executed again. Uncertain process ownership
retains the resource claim. A late authenticated supervisor result can resolve
that state. If the owning manager later confirms the orphan child process group
has stopped, it releases resources while retaining the `lost` outcome; it does
not invent an execution result. A new submission with the same idempotency key
returns the existing job; a different spec under that key is rejected.

`gpu_ids` lists the devices this run may request. Managers under the same
`data_root/runs/<run_id>/` automatically coordinate exclusive device allocation
across runs. `data_root/runtime/gpu-allocation.lock` is an empty flock coordination
file, not a global job database: all claims and results remain in each run's
`jobs/` directory. A scheduler briefly locks discovery plus publishing its own
claim. Contention leaves work queued instead of blocking a caller. Multi-GPU jobs
claim all requested devices together, so a queued job holds no partial allocation.

Closing/crashing a manager does not release its durable GPU claim. Other managers
may reuse the device once its detached supervisor has recorded authenticated
completion, even before the owner has restarted. A missing process or an uncertain
`lost` job is not treated as a free resource. This also covers service restarts
and independently launched Python processes, not only threads in one application.

`CUDA_VISIBLE_DEVICES` is set for each command, including an empty value for CPU
jobs. Device IDs must refer to the same execution host/resource namespace across
these runs. Separate data roots, custom standalone run paths outside `runs/`, and
other host applications are outside this coordinator; use an external scheduler
for those deployments. `backend="mock"` exercises the same exclusive allocation
and lifecycle without executing its command or using physical GPUs; its result
is labelled `mock`.

**The local POSIX backend is not a security sandbox.** GPU environment variables
are scheduling conventions, not access controls. Untrusted code can access host
files and escape a process group. Configure a container command with restricted
mounts/permissions for a stronger boundary; the existing task-agent sandbox is
unmodified. Give agents only template names and validated data parameters through
`ApprovedCommand`, not the arbitrary-command `JobSpec` constructor. Keep verifier
commands, code and labels outside candidate-writable locations, and collect
official scores through a separate trusted verifier adapter. Template binding
does not by itself validate path access or turn a local process into a sandbox.

Only one manager may own a run at a time. This implementation supports Linux and
macOS; a remote/container service can later implement the same job interface.
