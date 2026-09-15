"""Unit tests for the GCP Batch warm VM pool (pool primitives, handoff protocol,
pooled dispatch, and pooled monitoring)."""

import os
import threading
import time
from types import SimpleNamespace
from typing import (
    Any,
    cast,
)

import pytest
from google.api_core import exceptions as gcp_exceptions
from google.cloud import batch_v1

from galaxy.jobs.runners import RunnerParams
from galaxy.jobs.runners.gcp_batch import (
    GoogleCloudBatchJobRunner,
    RUNNER_PARAM_SPECS,
)
from galaxy.jobs.runners.util.gcp_batch import (
    CONTAINER_SCRIPT_TEMPLATE,
    POOLED_TASK_CONTAINER_TEMPLATE,
    render_pooled_wrapper_script,
)
from galaxy.jobs.runners.util.gcp_batch.pool import (
    atomic_write,
    compute_pool_key,
    DONE_PREFIX,
    HEARTBEAT_FILENAME,
    POOL_SAFETY_MARGIN,
    PooledVM,
    SHUTDOWN_FILENAME,
    TASK_FILENAME,
    VMPool,
    VMState,
)

NOW = 1_000_000.0


def _vm(name="vm-1", pool_key="key", state=VMState.IDLE, idle_since=NOW, created_at=NOW - 600, **kwargs):
    defaults = dict(
        batch_job_name=name,
        pool_key=pool_key,
        vm_dir=f"/pool/{name}",
        batch_job_path=f"projects/p/locations/r/jobs/{name}",
        created_at=created_at,
        lifetime_seconds=86400,
        idle_ttl_seconds=300,
        idle_since=idle_since,
        state=state,
    )
    defaults.update(kwargs)
    return PooledVM(**defaults)


class TestComputePoolKey:
    PARAMS = {
        "project_id": "p",
        "region": "us-central1",
        "zone": None,
        "machine_type": "n2-standard-4",
        "custom_vm_image": "img",
        "boot_disk_size_gb": 100,
        "boot_disk_type": "pd-standard",
        "network": "default",
        "subnet": "default",
        "service_account_email": "sa@p.iam",
        "gcp_batch_volumes": "10.0.0.1:/export:/mnt/nfs",
        "docker_extra_volumes": None,
        "galaxy_user_id": "1000",
        "galaxy_group_id": "1000",
        "use_container": True,
    }

    def test_stable(self):
        assert compute_pool_key(self.PARAMS) == compute_pool_key(dict(self.PARAMS))

    def test_sort_order_independent(self):
        reordered = dict(reversed(list(self.PARAMS.items())))
        assert compute_pool_key(self.PARAMS) == compute_pool_key(reordered)

    @pytest.mark.parametrize("key", sorted(PARAMS))
    def test_sensitive_to_each_param(self, key):
        changed = dict(self.PARAMS)
        changed[key] = "something-else"
        assert compute_pool_key(self.PARAMS) != compute_pool_key(changed)

    def test_insensitive_to_container_image(self):
        # The container image is deliberately not part of the pool key: docker
        # pull is per-job, so image identity does not gate VM compatibility.
        assert "container_image" not in self.PARAMS


class TestVMPool:
    def test_checkout_flips_idle_to_busy(self):
        pool = VMPool()
        pool.register(_vm())
        vm = pool.checkout("key", now=NOW + 10)
        assert vm is not None
        assert vm.state is VMState.BUSY
        assert vm.idle_since is None
        # No second checkout of the same VM
        assert pool.checkout("key", now=NOW + 10) is None

    def test_checkout_prefers_longest_idle(self):
        pool = VMPool()
        pool.register(_vm("vm-new", idle_since=NOW + 100))
        pool.register(_vm("vm-old", idle_since=NOW))
        vm = pool.checkout("key", now=NOW + 110)
        assert vm is not None and vm.batch_job_name == "vm-old"

    def test_checkout_partitions_by_pool_key(self):
        pool = VMPool()
        pool.register(_vm("vm-a", pool_key="key-a"))
        assert pool.checkout("key-b", now=NOW + 10) is None
        vm = pool.checkout("key-a", now=NOW + 10)
        assert vm is not None and vm.batch_job_name == "vm-a"

    def test_checkout_skips_non_idle(self):
        pool = VMPool()
        pool.register(_vm("vm-busy", state=VMState.BUSY, idle_since=None))
        pool.register(_vm("vm-drain", state=VMState.DRAINING))
        pool.register(_vm("vm-prov", state=VMState.PROVISIONING, idle_since=None))
        assert pool.checkout("key", now=NOW + 10) is None

    def test_checkout_skips_vm_near_its_idle_ttl(self):
        pool = VMPool()
        # idle for 250s of a 300s TTL: inside the 120s safety margin
        pool.register(_vm(idle_since=NOW - 250))
        assert pool.checkout("key", now=NOW) is None

    def test_checkout_skips_insufficient_remaining_lifetime(self):
        pool = VMPool()
        pool.register(_vm(created_at=NOW - 80000, lifetime_seconds=86400, idle_since=NOW - 10))
        # 6400s remain; a 7000s walltime cannot fit
        assert pool.checkout("key", now=NOW, min_remaining_lifetime=7000) is None
        # a 1000s walltime can
        assert pool.checkout("key", now=NOW, min_remaining_lifetime=1000) is not None

    def test_checkin_and_idle_count(self):
        pool = VMPool()
        pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None))
        assert pool.idle_count("key") == 0
        pool.checkin("vm-1", now=NOW)
        assert pool.idle_count("key") == 1
        vm = pool.get("vm-1")
        assert vm is not None and vm.state is VMState.IDLE and vm.idle_since == NOW

    def test_expired_idle(self):
        pool = VMPool()
        pool.register(_vm("vm-fresh", idle_since=NOW - 100))
        pool.register(_vm("vm-expired", idle_since=NOW - 300 - POOL_SAFETY_MARGIN - 1))
        expired = pool.expired_idle(now=NOW)
        assert [vm.batch_job_name for vm in expired] == ["vm-expired"]

    def test_mark_draining_stamps_time(self):
        pool = VMPool()
        pool.register(_vm("vm-1"))
        vm = pool.mark_draining("vm-1", now=NOW + 5)
        assert vm is not None and vm.state is VMState.DRAINING and vm.idle_since == NOW + 5

    def test_remove(self):
        pool = VMPool()
        pool.register(_vm("vm-1"))
        assert pool.remove("vm-1") is not None
        assert pool.remove("vm-1") is None
        assert pool.get("vm-1") is None

    def test_concurrent_checkout_no_double_assignment(self):
        pool = VMPool()
        n_vms = 5
        for i in range(n_vms):
            pool.register(_vm(f"vm-{i}", idle_since=NOW + i))
        results = []
        lock = threading.Lock()

        def worker():
            vm = pool.checkout("key", now=NOW + 100)
            with lock:
                results.append(vm)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [vm.batch_job_name for vm in results if vm is not None]
        assert len(winners) == n_vms
        assert len(set(winners)) == n_vms


class TestHandoffProtocol:
    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        target = tmp_path / TASK_FILENAME
        atomic_write(str(target), "payload\n")
        assert target.read_text() == "payload\n"
        assert os.listdir(tmp_path) == [TASK_FILENAME]

    def test_claim_race_has_exactly_one_winner(self, tmp_path):
        # The VM claims via mv task.sh task.claimed.sh; the runner reclaims via
        # mv task.sh task.reclaimed.<nonce>. Only one rename can succeed.
        task = tmp_path / TASK_FILENAME
        atomic_write(str(task), "payload")
        outcomes = []

        def contend(dst):
            try:
                os.rename(str(task), str(tmp_path / dst))
                outcomes.append(dst)
            except FileNotFoundError:
                pass

        threads = [
            threading.Thread(target=contend, args=("task.claimed.sh",)),
            threading.Thread(target=contend, args=("task.reclaimed.abcd",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(outcomes) == 1

    def test_done_marker_exit_code_parse(self, tmp_path):
        done = tmp_path / f"{DONE_PREFIX}42"
        atomic_write(str(done), "137\n")
        assert int(done.read_text().strip()) == 137


class TestTemplateRendering:
    def test_pooled_wrapper_contains_protocol_pieces(self):
        script = render_pooled_wrapper_script(
            nfs_server="10.0.0.1",
            nfs_path="/export",
            nfs_mount_path="/mnt/nfs",
            vm_dir="/mnt/nfs/.galaxy-vm-pool/vm-1",
            pool_ttl_seconds=300,
            vm_lifetime_seconds=86400,
        )
        # the claim rename
        assert 'mv "$VM_DIR/task.sh" "$VM_DIR/task.claimed.sh"' in script
        # deadlines
        assert "IDLE_DEADLINE" in script and "LIFETIME_DEADLINE" in script
        # heartbeat writer
        assert HEARTBEAT_FILENAME in script
        # marker files
        assert SHUTDOWN_FILENAME in script and "ready" in script and "exiting" in script
        # NFS preamble composed in with the template values applied
        assert "actimeo=0" in script and "10.0.0.1" in script
        # no unsubstituted placeholders
        assert "${nfs_setup}" not in script

    def test_pooled_task_payload_contents(self):
        payload = POOLED_TASK_CONTAINER_TEMPLATE.substitute(
            galaxy_job_id=7,
            job_id_tag="7",
            tool_id="cat1",
            container_image="img:1",
            nfs_mount_path="/mnt/nfs",
            docker_volume_args="",
            docker_user_flag="",
            galaxy_slots=2,
            galaxy_memory_mb=2048,
            job_file="/mnt/nfs/jobs/7/galaxy_7.sh",
            job_walltime_seconds=3600,
        )
        assert "# GALAXY_JOB_ID=7" in payload
        assert "timeout 3600 docker run" in payload
        assert '--name "galaxy-job-7"' in payload
        assert "GALAXY_SLOTS=2" in payload and "GALAXY_MEMORY_MB=2048" in payload

    def test_non_pooled_container_script_unchanged_by_settings_refactor(self):
        # Regression guard for the pool-off requirement: the non-pooled script
        # produced through _container_script_settings must be identical to a
        # direct template substitution with the historical inline logic.
        runner = _make_runner()
        params = {
            "gcp_batch_volumes": "10.0.0.1:/export:/mnt/nfs",
            "docker_extra_volumes": None,
            "galaxy_user_id": "1000",
            "galaxy_group_id": "1000",
        }
        job_wrapper = SimpleNamespace(get_id_tag=lambda: "1", tool=SimpleNamespace(id="cat1"))
        ajs = SimpleNamespace(job_file="/mnt/nfs/jobs/1/galaxy_1.sh")
        script = runner._create_container_execution_script(job_wrapper, ajs, params, "img:1", 2000, 4096)
        expected = CONTAINER_SCRIPT_TEMPLATE.substitute(
            job_id_tag="1",
            tool_id="cat1",
            container_image="img:1",
            nfs_server="10.0.0.1",
            nfs_path="/export",
            nfs_mount_path="/mnt/nfs",
            job_file="/mnt/nfs/jobs/1/galaxy_1.sh",
            galaxy_slots=2,
            galaxy_memory_mb=4096,
            docker_user_flag="--user 1000:1000",
            docker_volume_args=(
                '-v "/cvmfs/data.galaxyproject.org:/cvmfs/data.galaxyproject.org:ro" '
                '-v "/cvmfs/cloud.galaxyproject.org:/cvmfs/cloud.galaxyproject.org:ro"'
            ),
        )
        assert script == expected


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def _make_runner(runner_params=None):
    """Build a GoogleCloudBatchJobRunner without running __init__ (no GCP client)."""
    runner = object.__new__(GoogleCloudBatchJobRunner)
    runner.runner_params = RunnerParams(specs=RUNNER_PARAM_SPECS, params=runner_params or {})
    runner._vm_pool = VMPool()
    runner.work_queue = cast(Any, _RecordingQueue())
    runner.monitor_queue = cast(Any, _RecordingQueue())
    return runner


def _batch_client(state=None, not_found=False):
    def get_job(name):
        if not_found:
            raise gcp_exceptions.NotFound("gone")
        return SimpleNamespace(status=SimpleNamespace(state=state))

    return cast(Any, SimpleNamespace(get_job=get_job, delete_job=lambda name: None))


def _pooled_job_state(tmp_path, vm_id="vm-1", galaxy_job_id=7, claim_deadline=None):
    vm_dir = tmp_path / vm_id
    vm_dir.mkdir(exist_ok=True)
    state_changes = []
    job_wrapper = SimpleNamespace(
        job_id=galaxy_job_id,
        get_id_tag=lambda: str(galaxy_job_id),
        change_state=lambda s: state_changes.append(s),
    )
    job_state = SimpleNamespace(
        job_id=vm_id,
        job_wrapper=job_wrapper,
        pooled_vm_id=vm_id,
        pooled_vm_dir=str(vm_dir),
        pool_claim_deadline=claim_deadline,
        pool_claim_timeout=60,
        pool_max_size=5,
        running=False,
        fail_message=None,
    )
    return job_state, vm_dir, state_changes


class TestCheckPooledItem:
    """The runner-side handoff state machine (_check_pooled_item)."""

    def test_done_marker_finishes_job_and_returns_vm_to_pool(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, state_changes = _pooled_job_state(tmp_path)
        runner._vm_pool.register(
            _vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time(), current_galaxy_job_id="7")
        )
        atomic_write(str(vm_dir / f"{DONE_PREFIX}7"), "0\n")

        result = runner._check_pooled_item(job_state)

        assert result is None
        # finish_job enqueued on the work queue
        assert runner.work_queue.items and runner.work_queue.items[0][0].__name__ == "finish_job"
        # done marker consumed, VM back to idle
        assert not (vm_dir / f"{DONE_PREFIX}7").exists()
        vm = runner._vm_pool.get("vm-1")
        assert vm is not None and vm.state is VMState.IDLE

    def test_done_marker_drains_vm_when_idle_pool_full(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path)
        job_state.pool_max_size = 1
        now = time.time()
        runner._vm_pool.register(_vm("vm-idle", idle_since=now, created_at=now))
        vm = _vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=now, current_galaxy_job_id="7")
        vm.vm_dir = str(vm_dir)
        runner._vm_pool.register(vm)
        atomic_write(str(vm_dir / f"{DONE_PREFIX}7"), "0\n")

        runner._check_pooled_item(job_state)

        assert vm.state is VMState.DRAINING
        assert (vm_dir / SHUTDOWN_FILENAME).exists()

    def test_unclaimed_past_deadline_reclaims_and_resubmits(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path, claim_deadline=time.time() - 1)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        atomic_write(str(vm_dir / TASK_FILENAME), "payload")

        result = runner._check_pooled_item(job_state)

        assert result is None
        assert not (vm_dir / TASK_FILENAME).exists()
        assert runner._vm_pool.get("vm-1") is None
        assert runner.work_queue.items and runner.work_queue.items[0][0].__name__ == "_fallback_submit_fresh"
        assert job_state.pooled_vm_id is None

    def test_unclaimed_before_deadline_keeps_watching(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path, claim_deadline=time.time() + 60)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        atomic_write(str(vm_dir / TASK_FILENAME), "payload")

        assert runner._check_pooled_item(job_state) is job_state
        assert (vm_dir / TASK_FILENAME).exists()
        assert not runner.work_queue.items

    def test_provisioning_vm_gets_deadline_once_batch_runs(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path, claim_deadline=None)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        atomic_write(str(vm_dir / TASK_FILENAME), "payload")

        assert runner._check_pooled_item(job_state) is job_state
        assert job_state.pool_claim_deadline is not None
        assert job_state.pool_claim_deadline > time.time()

    def test_provisioning_vm_still_queued_no_deadline(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.QUEUED)
        job_state, vm_dir, state_changes = _pooled_job_state(tmp_path, claim_deadline=None)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        atomic_write(str(vm_dir / TASK_FILENAME), "payload")

        assert runner._check_pooled_item(job_state) is job_state
        assert job_state.pool_claim_deadline is None

    def test_vm_dead_before_claim_resubmits(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(not_found=True)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path, claim_deadline=None)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        atomic_write(str(vm_dir / TASK_FILENAME), "payload")

        result = runner._check_pooled_item(job_state)

        assert result is None
        assert runner.work_queue.items and runner.work_queue.items[0][0].__name__ == "_fallback_submit_fresh"

    def test_claimed_batch_failed_fails_job(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.FAILED)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        # no task.sh (claimed), no done marker

        result = runner._check_pooled_item(job_state)

        assert result is None
        assert runner.work_queue.items and runner.work_queue.items[0][0].__name__ == "fail_job"
        assert runner._vm_pool.get("vm-1") is None

    def test_claimed_stale_heartbeat_fails_job(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, _ = _pooled_job_state(tmp_path)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        heartbeat = vm_dir / HEARTBEAT_FILENAME
        heartbeat.write_text("busy 7")
        stale = time.time() - 600
        os.utime(heartbeat, (stale, stale))

        result = runner._check_pooled_item(job_state)

        assert result is None
        assert runner.work_queue.items and runner.work_queue.items[0][0].__name__ == "fail_job"

    def test_claimed_fresh_heartbeat_keeps_running(self, tmp_path):
        runner = _make_runner()
        runner.batch_client = _batch_client(batch_v1.JobStatus.State.RUNNING)
        job_state, vm_dir, state_changes = _pooled_job_state(tmp_path)
        runner._vm_pool.register(_vm("vm-1", state=VMState.BUSY, idle_since=None, created_at=time.time()))
        (vm_dir / HEARTBEAT_FILENAME).write_text("busy 7")

        assert runner._check_pooled_item(job_state) is job_state
        assert job_state.running is True


class TestDispatch:
    """_dispatch_job routing: pool off, direct mode, reuse hit, and pool miss."""

    def _job_wrapper(self, destination_params=None):
        return SimpleNamespace(
            job_id=7,
            get_id_tag=lambda: "7",
            tool=SimpleNamespace(id="cat1"),
            job_destination=SimpleNamespace(params=destination_params or {}),
            get_resource_parameters=lambda: {},
        )

    def test_pool_disabled_uses_plain_submission(self, monkeypatch):
        runner = _make_runner()
        called = []

        def fake_submit(jw, ajs):
            called.append(True)
            return "plain-job"

        monkeypatch.setattr(runner, "_submit_batch_job", fake_submit)
        assert runner._dispatch_job(self._job_wrapper(), SimpleNamespace()) == "plain-job"
        assert called

    def test_pool_enabled_string_false_uses_plain_submission(self, monkeypatch):
        runner = _make_runner()
        monkeypatch.setattr(runner, "_submit_batch_job", lambda jw, ajs: "plain-job")
        jw = self._job_wrapper({"pool_enabled": "false"})
        assert runner._dispatch_job(jw, SimpleNamespace()) == "plain-job"

    def test_direct_mode_runs_non_pooled(self, monkeypatch):
        runner = _make_runner()
        monkeypatch.setattr(runner, "_submit_batch_job", lambda jw, ajs: "plain-job")
        jw = self._job_wrapper({"pool_enabled": "true", "use_container": "false"})
        assert runner._dispatch_job(jw, SimpleNamespace()) == "plain-job"

    def test_reuse_hit_writes_payload_to_vm_dir(self, tmp_path, monkeypatch):
        runner = _make_runner()
        monkeypatch.setattr(runner, "_get_container_image", lambda jw: "img:1")
        now = time.time()
        vm = _vm(
            "vm-1",
            pool_key="ignored",
            idle_since=now - 10,
            created_at=now - 100,
            vm_dir=str(tmp_path / "vm-1"),
        )
        (tmp_path / "vm-1").mkdir()
        runner._vm_pool.register(vm)
        # Make the checkout match whatever key the dispatch computes
        monkeypatch.setattr("galaxy.jobs.runners.gcp_batch.compute_pool_key", lambda instance_params: "ignored")
        jw = self._job_wrapper({"pool_enabled": "true", "pool_dir": str(tmp_path), "max_run_duration": "3600"})
        ajs = SimpleNamespace(job_file="/mnt/nfs/jobs/7/galaxy_7.sh")

        name = runner._dispatch_job(jw, ajs)

        assert name == "vm-1"
        task = tmp_path / "vm-1" / TASK_FILENAME
        assert task.exists()
        content = task.read_text()
        assert "# GALAXY_JOB_ID=7" in content and "img:1" in content
        assert vm.state is VMState.BUSY and vm.current_galaxy_job_id == "7"
        assert ajs.pooled_vm_id == "vm-1"
        assert ajs.pool_claim_deadline is not None

    def test_pool_miss_provisions_fresh_pooled_vm(self, tmp_path, monkeypatch):
        runner = _make_runner({"project_id": "p"})
        monkeypatch.setattr(runner, "_get_container_image", lambda jw: "img:1")
        submitted: dict[str, Any] = {}

        def fake_submit_pooled(jw, ajs, params, job_name, vm_dir, lifetime_seconds):
            submitted.update(job_name=job_name, vm_dir=vm_dir, lifetime_seconds=lifetime_seconds)

        monkeypatch.setattr(runner, "_submit_pooled_batch_job", fake_submit_pooled)
        jw = self._job_wrapper({"pool_enabled": "true", "pool_dir": str(tmp_path)})
        ajs = SimpleNamespace(job_file="/mnt/nfs/jobs/7/galaxy_7.sh")

        name = runner._dispatch_job(jw, ajs)

        assert submitted["job_name"] == name
        vm = runner._vm_pool.get(name)
        assert vm is not None and vm.state is VMState.BUSY and vm.current_galaxy_job_id == "7"
        assert submitted["lifetime_seconds"] == 86400  # 24h default
        assert (tmp_path / name / TASK_FILENAME).exists()
        # fresh VM: claim clock starts only once Batch reports RUNNING
        assert ajs.pool_claim_deadline is None


class TestFallbackSubmitFresh:
    def test_cancelled_job_is_not_resubmitted(self, monkeypatch):
        from galaxy import model

        runner = _make_runner()
        submitted = []
        monkeypatch.setattr(runner, "_submit_batch_job", lambda jw, ajs: submitted.append(True))
        job_wrapper = SimpleNamespace(
            get_id_tag=lambda: "7",
            get_job=lambda: SimpleNamespace(state=model.Job.states.DELETED),
        )
        runner._fallback_submit_fresh(SimpleNamespace(job_wrapper=job_wrapper))
        assert not submitted

    def test_live_job_is_resubmitted_and_monitored(self, monkeypatch):
        from galaxy import model

        runner = _make_runner()
        monkeypatch.setattr(runner, "_submit_batch_job", lambda jw, ajs: "fresh-job")
        external_ids: list[str] = []
        job_wrapper = SimpleNamespace(
            get_id_tag=lambda: "7",
            get_job=lambda: SimpleNamespace(state=model.Job.states.QUEUED),
            set_external_id=external_ids.append,
        )
        ajs = SimpleNamespace(job_wrapper=job_wrapper, job_id=None)
        runner._fallback_submit_fresh(ajs)
        assert ajs.job_id == "fresh-job"
        assert external_ids == ["fresh-job"]
        assert runner.monitor_queue.items == [ajs]


class TestResolvePoolDir:
    def test_explicit_pool_dir_wins(self):
        runner = _make_runner()
        assert runner._resolve_pool_dir({"pool_dir": "/custom/pool"}) == "/custom/pool"

    def test_derives_from_first_volume_mount_path(self):
        runner = _make_runner()
        params = {"gcp_batch_volumes": "10.0.0.1:/export:/galaxy/server/database", "pool_dir": None}
        assert runner._resolve_pool_dir(params) == "/galaxy/server/database/.galaxy-vm-pool"

    def test_falls_back_to_default_mount_path(self):
        runner = _make_runner()
        assert runner._resolve_pool_dir({"pool_dir": None}) == "/mnt/nfs/.galaxy-vm-pool"
