"""Warm VM pool primitives for the GCP Batch job runner.

GCP Batch cannot attach new jobs to already-running VMs, so the warm pool works
by keeping the VM's Batch task script alive: after a Galaxy job finishes, the
pooled wrapper script idles up to a configurable TTL watching a handoff
directory on the shared NFS mount for the next job payload. The runner keeps an
in-memory registry of these idle-but-running Batch jobs (one VM each) and hands
new work to a matching VM by writing the job payload into its handoff
directory.

This module holds the pure, unit-testable pieces: the pool key computation, the
in-memory pool registry, the handoff protocol constants, and the atomic-write
helper. Nothing here imports GCP libraries.

Handoff directory layout (one directory per pooled VM, on shared NFS)::

    <pool_dir>/<batch_job_name>/
      task.sh             # runner -> VM: next job payload (atomic write+rename)
      task.claimed.sh     # VM's rename target = the claim/ack
      done-<galaxy_job_id># VM -> runner: job finished; content = exit code
      heartbeat           # VM rewrites every 15s: "idle" | "busy <galaxy_job_id>"
      ready               # VM: wrapper booted, loop entered
      exiting             # VM: exiting (TTL/lifetime), do not assign
      shutdown            # runner -> VM: exit at next loop iteration
      cancel              # runner -> VM: kill running container, then exit

The claim is a rename race with exactly one winner: the VM claims a payload via
``mv task.sh task.claimed.sh``; if the claim takes too long the runner reclaims
via ``mv task.sh task.reclaimed.<nonce>``. Rename atomicity on the NFS server
guarantees at most one of the two renames succeeds, which is what makes double
execution impossible.
"""

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# Safety margin (seconds) kept between the runner's view of a VM's deadlines and
# the wrapper script's own deadlines, so the runner never assigns work to a VM
# that is about to tear itself down.
POOL_SAFETY_MARGIN = 120

# Heartbeat cadence of the wrapper script and the staleness threshold beyond
# which the runner considers a VM dead. The threshold leaves room for NFS
# attribute-caching lag on the handler side on top of missed heartbeats.
HEARTBEAT_INTERVAL = 15
HEARTBEAT_STALE_AFTER = 90

# Handoff protocol filenames.
TASK_FILENAME = "task.sh"
CLAIMED_FILENAME = "task.claimed.sh"
DONE_PREFIX = "done-"
HEARTBEAT_FILENAME = "heartbeat"
READY_FILENAME = "ready"
EXITING_FILENAME = "exiting"
SHUTDOWN_FILENAME = "shutdown"
CANCEL_FILENAME = "cancel"


def compute_pool_key(instance_params: dict) -> str:
    """Return a stable hash of the instance parameters that define VM compatibility.

    Two jobs may share a pooled VM only if every parameter that affects the
    provisioned VM (project, region, machine type, image, disks, network,
    volumes, user mapping, ...) is identical. The caller assembles that dict;
    this function only guarantees a stable, order-independent digest of it.
    """
    serialized = json.dumps(instance_params, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class VMState(str, Enum):
    PROVISIONING = "provisioning"
    BUSY = "busy"
    IDLE = "idle"
    DRAINING = "draining"


@dataclass
class PooledVM:
    """Runner-side record of one pooled Batch job (= one VM)."""

    batch_job_name: str  # also the vm_dir basename and the Galaxy external job id
    pool_key: str
    vm_dir: str
    batch_job_path: str  # fully qualified projects/.../locations/.../jobs/<name>
    created_at: float
    lifetime_seconds: float  # Batch max_run_duration of the pooled job
    idle_ttl_seconds: float  # wrapper's idle deadline; VM exits after this much idle time
    idle_since: Optional[float] = None
    state: VMState = VMState.PROVISIONING
    current_galaxy_job_id: Optional[str] = None

    def remaining_lifetime(self, now: float) -> float:
        return (self.created_at + self.lifetime_seconds) - now


class VMPool:
    """Thread-safe in-memory registry of pooled VMs, keyed by batch job name.

    A single lock guards the dict; all state transitions on registered VMs go
    through these methods so checkout is atomic (no two Galaxy jobs can check
    out the same idle VM).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vms: dict[str, PooledVM] = {}

    def register(self, vm: PooledVM) -> None:
        with self._lock:
            self._vms[vm.batch_job_name] = vm

    def checkout(self, pool_key: str, now: float, min_remaining_lifetime: float = 0.0) -> Optional[PooledVM]:
        """Atomically claim the longest-idle compatible VM, flipping it to BUSY.

        VMs are skipped when they are within ``POOL_SAFETY_MARGIN`` of their own
        idle TTL (the wrapper may be about to exit) or when their remaining
        lifetime cannot cover the job's walltime plus the margin. Returns None
        when no compatible idle VM is available.
        """
        with self._lock:
            candidates = []
            for vm in self._vms.values():
                if vm.state is not VMState.IDLE or vm.pool_key != pool_key:
                    continue
                # Guard against pathological configs where the TTL itself is
                # smaller than the safety margin: never let the margin consume
                # more than half the TTL.
                margin = min(POOL_SAFETY_MARGIN, vm.idle_ttl_seconds / 2)
                if vm.idle_since is not None and (now - vm.idle_since) >= (vm.idle_ttl_seconds - margin):
                    continue
                if vm.remaining_lifetime(now) < min_remaining_lifetime + POOL_SAFETY_MARGIN:
                    continue
                candidates.append(vm)
            if not candidates:
                return None
            vm = min(candidates, key=lambda v: v.idle_since if v.idle_since is not None else v.created_at)
            vm.state = VMState.BUSY
            vm.idle_since = None
            return vm

    def checkin(self, batch_job_name: str, now: float) -> Optional[PooledVM]:
        """Return a VM to the idle pool after its job completed."""
        with self._lock:
            vm = self._vms.get(batch_job_name)
            if vm is not None:
                vm.state = VMState.IDLE
                vm.idle_since = now
                vm.current_galaxy_job_id = None
            return vm

    def mark_draining(self, batch_job_name: str, now: float) -> Optional[PooledVM]:
        """Mark a VM as draining (shutdown requested, awaiting teardown).

        ``idle_since`` is restamped so callers can measure how long the drain
        has been pending and force-delete the Batch job if the VM ignores it.
        """
        with self._lock:
            vm = self._vms.get(batch_job_name)
            if vm is not None:
                vm.state = VMState.DRAINING
                vm.idle_since = now
            return vm

    def remove(self, batch_job_name: str) -> Optional[PooledVM]:
        with self._lock:
            return self._vms.pop(batch_job_name, None)

    def get(self, batch_job_name: str) -> Optional[PooledVM]:
        with self._lock:
            return self._vms.get(batch_job_name)

    def idle_count(self, pool_key: Optional[str] = None) -> int:
        with self._lock:
            return sum(
                1
                for vm in self._vms.values()
                if vm.state is VMState.IDLE and (pool_key is None or vm.pool_key == pool_key)
            )

    def expired_idle(self, now: float) -> list[PooledVM]:
        """List idle VMs whose wrapper should already have exited on its own TTL."""
        with self._lock:
            return [
                vm
                for vm in self._vms.values()
                if vm.state is VMState.IDLE
                and vm.idle_since is not None
                and (now - vm.idle_since) > vm.idle_ttl_seconds + POOL_SAFETY_MARGIN
            ]

    def all_vms(self) -> list[PooledVM]:
        with self._lock:
            return list(self._vms.values())


def atomic_write(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically (temp file + rename).

    The temporary file lives in the same directory so the rename stays within
    one filesystem (and one NFS export). Readers polling for ``path`` therefore
    never observe a partially written file.
    """
    tmp_path = f"{path}.tmp.{os.urandom(4).hex()}"
    with open(tmp_path, "w") as f:
        f.write(content)
    os.rename(tmp_path, path)
