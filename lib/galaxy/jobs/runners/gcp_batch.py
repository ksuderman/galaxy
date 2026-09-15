"""
Google Cloud Batch Job Runner

This runner submits Galaxy jobs to Google Cloud Batch for execution.
"""

import json
import logging
import os
import shutil
import time
from typing import (
    Any,
)

from google.api_core import exceptions as gcp_exceptions
from google.auth import default
from google.cloud import batch_v1

from galaxy import model
from galaxy.jobs.runners import (
    AsynchronousJobRunner,
    AsynchronousJobState,
)
from galaxy.jobs.runners.util.gcp_batch import (
    compute_machine_type,
    CONTAINER_SCRIPT_TEMPLATE,
    convert_cpu_to_milli,
    convert_duration_to_seconds,
    convert_memory_to_mib,
    DEFAULT_CVMFS_DOCKER_VOLUME,
    DEFAULT_MAX_RUN_DURATION,
    DEFAULT_MEMORY_MIB,
    DEFAULT_NFS_MOUNT_PATH,
    DEFAULT_NFS_PATH,
    DIRECT_SCRIPT_TEMPLATE,
    parse_docker_volumes_param,
    parse_volumes_param,
    POOLED_TASK_CONTAINER_TEMPLATE,
    render_pooled_wrapper_script,
    resolve_max_run_duration,
    sanitize_label_value,
)
from galaxy.jobs.runners.util.gcp_batch.pool import (
    atomic_write,
    CANCEL_FILENAME,
    compute_pool_key,
    DONE_PREFIX,
    EXITING_FILENAME,
    HEARTBEAT_FILENAME,
    HEARTBEAT_STALE_AFTER,
    POOL_SAFETY_MARGIN,
    PooledVM,
    SHUTDOWN_FILENAME,
    TASK_FILENAME,
    VMPool,
    VMState,
)
from galaxy.util import string_as_bool

log = logging.getLogger(__name__)

__all__ = ("GoogleCloudBatchJobRunner",)


# Runner parameter specifications. The defaults defined here are served lazily by
# the ParamsWithSpecs defaultdict via __missing__, which is only triggered by
# subscript access (runner_params[key]) -- not by .get(). See _get_job_params.
RUNNER_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "project_id": dict(map=str, default=None),
    "region": dict(map=str, default="us-central1"),
    "zone": dict(map=str, default=None),
    "service_account_file": dict(map=str, default=None),
    "service_account_email": dict(map=str, default=None),
    "machine_type": dict(map=str, default="n2-standard-4"),
    "boot_disk_size_gb": dict(map=int, default=100),
    "boot_disk_type": dict(map=str, default="pd-standard"),
    "max_retry_count": dict(map=int, default=3),
    "max_run_duration": dict(map=str, default=DEFAULT_MAX_RUN_DURATION),
    "polling_interval": dict(map=int, default=30),
    # Volume configuration (generic format: "server:/remote_path:/mount_path[:ro],...")
    "gcp_batch_volumes": dict(map=str, default=None),
    # Extra docker volume mounts (format: "/host/path:/container/path[:ro],...")
    "docker_extra_volumes": dict(map=str, default=None),
    # Network configuration for NFS access
    "network": dict(map=str, default="default"),
    "subnet": dict(map=str, default="default"),
    # Compute resource configuration (defaults - will be overridden by job requirements)
    "vcpu": dict(map=float, default=1.0),
    "memory_mib": dict(map=int, default=DEFAULT_MEMORY_MIB),
    # Job-specific resource requests (same as Kubernetes runner)
    "requests_cpu": dict(map=str, default=None),
    "requests_memory": dict(map=str, default=None),
    "limits_cpu": dict(map=str, default=None),
    "limits_memory": dict(map=str, default=None),
    # Container execution settings
    "use_container": dict(map=bool, default=True),
    "galaxy_user_id": dict(
        map=str, valid=lambda s: s == "$uid" or isinstance(s, int) or not s or str(s).isdigit(), default=None
    ),
    "galaxy_group_id": dict(
        map=str, valid=lambda s: s == "$gid" or isinstance(s, int) or not s or str(s).isdigit(), default=None
    ),
    # Custom VM image (optional)
    "custom_vm_image": dict(map=str, default=None),
    # Job cleanup: if true, delete GCP Batch jobs after Galaxy marks them complete
    "delete_completed_jobs": dict(map=bool, default=True),
    # Prefix for GCP Batch job IDs (helps identify which Galaxy server submitted a job)
    "job_id_prefix": dict(map=str, default="galaxy-job"),
    # Object store fallback (for future use)
    "use_object_store": dict(map=bool, default=False),
    "object_store_path": dict(map=str, default=None),
    # Warm VM pool (see util/gcp_batch/pool.py). Disabled by default; with
    # pool_enabled false the runner behaves exactly as before. Pooling only
    # applies to containerized jobs (use_container=true).
    "pool_enabled": dict(map=bool, default=False),
    # Idle seconds before a pooled VM's wrapper exits and the VM is torn down
    "pool_ttl_seconds": dict(map=int, default=300),
    # Max idle VMs retained per handler (running jobs are uncapped, as today)
    "pool_max_size": dict(map=int, default=5),
    # Batch max_run_duration for pooled VMs (spans all jobs on the VM plus idle time)
    "pool_max_vm_lifetime": dict(map=str, default="24h"),
    # How long to wait for a VM to claim a handed-off task before resubmitting fresh
    "pool_claim_timeout_seconds": dict(map=int, default=60),
    # Handoff root on the shared NFS mount; default derives
    # <first gcp_batch_volume mount_path>/.galaxy-vm-pool
    "pool_dir": dict(map=str, default=None),
}


class GoogleCloudBatchJobRunner(AsynchronousJobRunner):
    """
    Job runner that submits jobs to Google Cloud Batch.
    """

    runner_name = "GoogleCloudBatchJobRunner"

    def __init__(self, app, nworkers, **kwargs):
        """Initialize the Google Cloud Batch job runner."""
        log.debug("Starting GoogleCloudBatchJobRunner.__init__")

        kwargs.update({"runner_param_specs": RUNNER_PARAM_SPECS})
        super().__init__(app, nworkers, **kwargs)

        # Initialize Google Cloud Batch client
        self._init_batch_client()

        # In-memory registry of warm pooled VMs (empty and inert unless a
        # destination enables pool_enabled).
        self._vm_pool = VMPool()

        log.info(
            "GoogleCloudBatchJobRunner initialized for project: %s",
            self.runner_params.get("project_id", "Not specified"),
        )
        log.debug("Finished GoogleCloudBatchJobRunner.__init__")

    def _init_batch_client(self):
        """Initialize the Google Cloud Batch client."""
        # Set up authentication
        if service_account_file := self.runner_params.get("service_account_file"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = service_account_file

        try:
            credentials, project = default()
            self.batch_client = batch_v1.BatchServiceClient(credentials=credentials)
        except Exception as e:
            log.error("Failed to initialize Google Cloud Batch client: %s", e)
            raise

        # Set project ID
        if not self.runner_params.get("project_id"):
            if project:
                self.runner_params["project_id"] = project
            else:
                raise ValueError("Google Cloud project ID not specified and could not be determined from credentials")

        log.info("Google Cloud Batch client initialized successfully")

    @property
    def monitor_sleep_time(self):
        """Throttle the monitor loop so it polls the GCP Batch API no more often
        than the configured ``polling_interval``.

        The base ``AsynchronousJobRunner.monitor()`` loop sleeps this long between
        sweeps; each runner has its own monitor thread, so this only affects the
        GCP Batch runner. Item access (not ``.get()``) is used so the spec default
        of 30 resolves through ``ParamsWithSpecs.__missing__``.
        """
        return max(self.app.config.job_runner_monitor_sleep, int(self.runner_params["polling_interval"]))

    def queue_job(self, job_wrapper):
        """Queue a job for execution on Google Cloud Batch."""
        log.debug("Starting queue_job for Galaxy job %s", job_wrapper.get_id_tag())

        # Create AsynchronousJobState
        ajs = AsynchronousJobState(
            files_dir=job_wrapper.working_directory,
            job_wrapper=job_wrapper,
            job_destination=job_wrapper.job_destination,
        )

        # Create required output files
        with open(ajs.output_file, "w"):
            pass
        with open(ajs.error_file, "w"):
            pass

        # Prepare the job
        if not self.prepare_job(
            job_wrapper,
            include_metadata=False,
            modify_command_for_container=False,
            stream_stdout_stderr=True,
        ):
            log.error("Failed to prepare job %s", job_wrapper.get_id_tag())
            return

        # Generate job script
        script = self.get_job_file(
            job_wrapper, exit_code_path=ajs.exit_code_file, shell=job_wrapper.shell, galaxy_virtual_env=None
        )

        try:
            self.write_executable_script(ajs.job_file, script, job_io=job_wrapper.job_io)
        except Exception as e:
            log.error("Failed to write job script for job %s: %s", job_wrapper.get_id_tag(), e)
            job_wrapper.fail("Failed to write job script")
            return

        try:
            # Submit job to Google Cloud Batch (via the warm VM pool if enabled)
            batch_job_name = self._dispatch_job(job_wrapper, ajs)

            # Store runner information for tracking if Galaxy restarts
            ajs.job_id = batch_job_name
            job_wrapper.set_external_id(batch_job_name)
            self.monitor_queue.put(ajs)

            log.info("Successfully queued Galaxy job %s as Batch job %s", job_wrapper.get_id_tag(), batch_job_name)

        except Exception as e:
            log.error("Failed to submit job %s to Google Cloud Batch: %s", job_wrapper.get_id_tag(), e)
            job_wrapper.fail(f"Failed to submit job to Google Cloud Batch: {e}")

        log.debug("Finished queue_job for Galaxy job %s", job_wrapper.get_id_tag())

    def _submit_batch_job(self, job_wrapper, ajs) -> str:
        """Submit a job to Google Cloud Batch and return the job name."""
        log.debug("Starting _submit_batch_job for job %s", job_wrapper.get_id_tag())

        # Get job destination parameters
        job_destination = job_wrapper.job_destination
        params = self._get_job_params(job_destination)

        # Generate unique job name
        prefix = params.get("job_id_prefix") or "galaxy-job"
        job_name = f"{prefix}-{int(time.time())}-{os.urandom(4).hex()}-{job_wrapper.get_id_tag()}"

        # Create the batch job specification
        batch_job = self._create_batch_job_spec(job_wrapper, ajs, params)

        # Submit the job
        request = batch_v1.CreateJobRequest()
        request.parent = f"projects/{params['project_id']}/locations/{params['region']}"
        request.job_id = job_name
        request.job = batch_job

        # Write job parameters and request to JSON files for debugging
        try:
            self._write_debug_files(job_wrapper, ajs, params, request, job_name)
        except Exception as e:
            log.warning("Failed to write debug files for job %s: %s", job_wrapper.get_id_tag(), e)

        try:
            operation = self.batch_client.create_job(request=request)
            log.info("Submitted Batch job %s, operation: %s", job_name, operation.name)
            return job_name
        except Exception as e:
            log.error("Failed to create Batch job: %s", e)
            raise

    def _dispatch_job(self, job_wrapper, ajs) -> str:
        """Route a job to a warm pooled VM or to a fresh Batch job.

        With pooling disabled (the default) this is exactly the pre-pool
        submission path. Pooling only supports containerized jobs; direct-mode
        destinations log a note and run non-pooled.
        """
        params = self._get_job_params(job_wrapper.job_destination)
        # Destination values arrive as strings, so bool("false") would be True;
        # string_as_bool handles both real bools and strings.
        if not string_as_bool(params.get("pool_enabled") or False):
            return self._submit_batch_job(job_wrapper, ajs)
        if not string_as_bool(params.get("use_container", True)):
            log.info(
                "VM pooling only supports containerized jobs; job %s runs non-pooled (use_container=false)",
                job_wrapper.get_id_tag(),
            )
            return self._submit_batch_job(job_wrapper, ajs)
        return self._dispatch_pooled_job(job_wrapper, ajs, params)

    def _dispatch_pooled_job(self, job_wrapper, ajs, params) -> str:
        """Hand the job to an idle pooled VM, or provision a fresh pooled VM."""
        cpu_milli, memory_mib = self._get_job_resources(job_wrapper, params)
        machine_type = compute_machine_type(cpu_milli, memory_mib)
        walltime = resolve_max_run_duration(
            job_wrapper.job_destination.params, params, job_wrapper.get_resource_parameters()
        )
        walltime_seconds = int(walltime.rstrip("s"))
        lifetime_seconds = int(convert_duration_to_seconds(params["pool_max_vm_lifetime"]).rstrip("s"))
        if walltime_seconds + POOL_SAFETY_MARGIN >= lifetime_seconds:
            # Checkout requires remaining VM lifetime >= walltime + margin, so a
            # walltime this close to the VM lifetime means no VM can ever be
            # reused: every job will provision (and pay for) a fresh pooled VM.
            log.warning(
                "Job %s walltime (%ss) is not safely below pool_max_vm_lifetime (%ss); "
                "pooled VM reuse is impossible with these settings - set max_run_duration/walltime "
                "well below pool_max_vm_lifetime",
                job_wrapper.get_id_tag(),
                walltime_seconds,
                lifetime_seconds,
            )
        pool_key = compute_pool_key(self._pool_key_params(params, machine_type))
        payload = self._render_task_payload(job_wrapper, ajs, params, cpu_milli, memory_mib, walltime_seconds)
        claim_timeout = int(params["pool_claim_timeout_seconds"])

        now = time.time()
        vm = self._vm_pool.checkout(pool_key, now=now, min_remaining_lifetime=walltime_seconds)
        if vm is not None:
            vm.current_galaxy_job_id = str(job_wrapper.job_id)
            atomic_write(os.path.join(vm.vm_dir, TASK_FILENAME), payload)
            self._set_pooled_state(ajs, params, vm.batch_job_name, vm.vm_dir, claim_deadline=now + claim_timeout)
            log.info("Reusing pooled VM %s for job %s", vm.batch_job_name, job_wrapper.get_id_tag())
            return vm.batch_job_name

        # Miss: provision a fresh pooled VM with the payload already staged in
        # its handoff directory; the wrapper claims it on boot.
        prefix = params.get("job_id_prefix") or "galaxy-job"
        job_name = f"{prefix}-{int(now)}-{os.urandom(4).hex()}-{job_wrapper.get_id_tag()}"
        vm_dir = os.path.join(self._resolve_pool_dir(params), job_name)
        os.makedirs(vm_dir, exist_ok=True)
        atomic_write(os.path.join(vm_dir, TASK_FILENAME), payload)

        try:
            self._submit_pooled_batch_job(job_wrapper, ajs, params, job_name, vm_dir, lifetime_seconds)
        except Exception:
            shutil.rmtree(vm_dir, ignore_errors=True)
            raise

        self._vm_pool.register(
            PooledVM(
                batch_job_name=job_name,
                pool_key=pool_key,
                vm_dir=vm_dir,
                batch_job_path=f"projects/{params['project_id']}/locations/{params['region']}/jobs/{job_name}",
                created_at=now,
                lifetime_seconds=lifetime_seconds,
                idle_ttl_seconds=int(params["pool_ttl_seconds"]),
                state=VMState.BUSY,
                current_galaxy_job_id=str(job_wrapper.job_id),
            )
        )
        # No claim deadline yet: VM boot takes minutes, so the claim clock only
        # starts once the Batch job reports RUNNING (see _check_pooled_item).
        self._set_pooled_state(ajs, params, job_name, vm_dir, claim_deadline=None)
        log.info("Provisioned fresh pooled VM %s for job %s", job_name, job_wrapper.get_id_tag())
        return job_name

    def _set_pooled_state(self, ajs, params, vm_id, vm_dir, claim_deadline):
        """Stash the pooled-handoff tracking state on the job's AsynchronousJobState."""
        ajs.pooled_vm_id = vm_id
        ajs.pooled_vm_dir = vm_dir
        ajs.pool_claim_deadline = claim_deadline
        ajs.pool_claim_timeout = int(params["pool_claim_timeout_seconds"])
        ajs.pool_max_size = int(params["pool_max_size"])

    def _pool_key_params(self, params, machine_type) -> dict[str, Any]:
        """The instance parameters that define whether two jobs may share a VM.

        The resolved machine type is used (not raw cpu/mem requests) so that
        different requests mapping to the same machine type share VMs. The
        container image is deliberately excluded: docker pull is per-job, so
        same-image reuse is a cache win, not a correctness requirement.
        """
        return {
            "project_id": params.get("project_id"),
            "region": params.get("region"),
            "zone": params.get("zone"),
            "machine_type": machine_type,
            "custom_vm_image": params.get("custom_vm_image"),
            "boot_disk_size_gb": params.get("boot_disk_size_gb"),
            "boot_disk_type": params.get("boot_disk_type"),
            "network": params.get("network"),
            "subnet": params.get("subnet"),
            "service_account_email": params.get("service_account_email"),
            "gcp_batch_volumes": params.get("gcp_batch_volumes"),
            "docker_extra_volumes": params.get("docker_extra_volumes"),
            "galaxy_user_id": params.get("galaxy_user_id"),
            "galaxy_group_id": params.get("galaxy_group_id"),
            "use_container": True,
        }

    def _resolve_pool_dir(self, params) -> str:
        """Resolve the handoff root directory (must be on the shared NFS mount)."""
        if pool_dir := params.get("pool_dir"):
            return pool_dir
        volumes_param = params.get("gcp_batch_volumes")
        parsed_volumes = parse_volumes_param(volumes_param) if volumes_param else []
        mount_path = parsed_volumes[0]["mount_path"] if parsed_volumes else DEFAULT_NFS_MOUNT_PATH
        return os.path.join(mount_path, ".galaxy-vm-pool")

    def _render_task_payload(self, job_wrapper, ajs, params, cpu_milli, memory_mib, walltime_seconds) -> str:
        """Render the per-job payload dropped into a pooled VM's handoff dir."""
        container_image = self._get_container_image(job_wrapper)
        settings = self._container_script_settings(params, cpu_milli)
        return POOLED_TASK_CONTAINER_TEMPLATE.substitute(
            galaxy_job_id=job_wrapper.job_id,
            job_id_tag=job_wrapper.get_id_tag(),
            tool_id=job_wrapper.tool.id if job_wrapper.tool else "unknown",
            container_image=container_image,
            nfs_mount_path=settings["nfs_mount_path"],
            docker_volume_args=settings["docker_volume_args"],
            docker_user_flag=settings["docker_user_flag"],
            galaxy_slots=settings["galaxy_slots"],
            galaxy_memory_mb=memory_mib,
            job_file=ajs.job_file,
            job_walltime_seconds=walltime_seconds,
        )

    def _submit_pooled_batch_job(self, job_wrapper, ajs, params, job_name, vm_dir, lifetime_seconds) -> None:
        """Submit the Batch job that boots a pooled VM running the idle wrapper.

        Reuses the standard job spec (volumes, network, machine type, disks,
        service account) and overrides only what pooling changes: the runnable
        script, no retries, the VM-lifetime run duration, and a pool label.
        """
        batch_job = self._create_batch_job_spec(job_wrapper, ajs, params)
        cpu_milli, _ = self._get_job_resources(job_wrapper, params)
        settings = self._container_script_settings(params, cpu_milli)
        wrapper_script = render_pooled_wrapper_script(
            nfs_server=settings["nfs_server"],
            nfs_path=settings["nfs_path"],
            nfs_mount_path=settings["nfs_mount_path"],
            vm_dir=vm_dir,
            pool_ttl_seconds=int(params["pool_ttl_seconds"]),
            vm_lifetime_seconds=lifetime_seconds,
        )
        task_spec = batch_job.task_groups[0].task_spec
        task_spec.runnables[0].script.text = wrapper_script
        # A Batch retry would boot a duplicate wrapper watching the same
        # handoff directory; never retry pooled VMs.
        task_spec.max_retry_count = 0
        task_spec.max_run_duration = f"{lifetime_seconds}s"
        batch_job.labels["galaxy-pooled"] = "true"

        request = batch_v1.CreateJobRequest()
        request.parent = f"projects/{params['project_id']}/locations/{params['region']}"
        request.job_id = job_name
        request.job = batch_job

        try:
            self._write_debug_files(job_wrapper, ajs, params, request, job_name)
        except Exception as e:
            log.warning("Failed to write debug files for job %s: %s", job_wrapper.get_id_tag(), e)

        operation = self.batch_client.create_job(request=request)
        log.info("Submitted pooled Batch job %s, operation: %s", job_name, operation.name)

    def _get_job_params(self, job_destination) -> dict[str, Any]:
        """Extract job parameters from destination and runner configuration."""
        log.debug("Starting _get_job_params")
        params = {}

        # Copy from runner params with destination overrides
        for key in [
            "project_id",
            "region",
            "zone",
            "machine_type",
            "boot_disk_size_gb",
            "boot_disk_type",
            "max_retry_count",
            "max_run_duration",
            "gcp_batch_volumes",
            "docker_extra_volumes",
            "network",
            "subnet",
            "vcpu",
            "memory_mib",
            "use_container",
            "galaxy_user_id",
            "galaxy_group_id",
            "custom_vm_image",
            "use_object_store",
            "object_store_path",
            "service_account_email",
            "job_id_prefix",
            "pool_enabled",
            "pool_ttl_seconds",
            "pool_max_size",
            "pool_max_vm_lifetime",
            "pool_claim_timeout_seconds",
            "pool_dir",
        ]:
            # Subscript access on runner_params (a defaultdict) so unset keys fall
            # back to the spec defaults defined in runner_param_specs; .get() would
            # bypass __missing__ and yield None instead of the configured default.
            params[key] = job_destination.params.get(key, self.runner_params[key])

        log.debug("Finished _get_job_params")
        return params

    def _create_batch_job_spec(self, job_wrapper, ajs, params):
        """Create a Google Cloud Batch job specification."""
        log.debug("Starting _create_batch_job_spec for job %s", job_wrapper.get_id_tag())

        # Get container image from Galaxy's container finder
        container_image = self._get_container_image(ajs.job_wrapper)
        log.debug("Using container image: %s for job %s", container_image, job_wrapper.get_id_tag())

        # Get compute resources first so we can pass them to script creation
        cpu_milli, memory_mib = self._get_job_resources(job_wrapper, params)

        # Get max run duration (resolves per-job from destination, resource params, or default)
        max_run_duration = resolve_max_run_duration(
            job_wrapper.job_destination.params, params, job_wrapper.get_resource_parameters()
        )

        # Create the execution script based on whether we use containers or not
        if params.get("use_container", True):
            execution_script = self._create_container_execution_script(
                job_wrapper, ajs, params, container_image, cpu_milli, memory_mib
            )
        else:
            execution_script = self._create_direct_execution_script(job_wrapper, ajs, params, cpu_milli, memory_mib)

        # Create runnable with script execution
        runnable = batch_v1.Runnable()
        runnable.script = batch_v1.Runnable.Script()
        runnable.script.text = execution_script

        # Create task specification
        task_spec = batch_v1.TaskSpec()
        task_spec.runnables = [runnable]
        task_spec.max_retry_count = params["max_retry_count"]
        task_spec.max_run_duration = max_run_duration

        # Set compute resources
        compute_resource = batch_v1.ComputeResource()
        compute_resource.cpu_milli = cpu_milli
        compute_resource.memory_mib = memory_mib
        task_spec.compute_resource = compute_resource

        log.debug(
            "Configured compute resources for job %s: %d mCPU, %d MiB memory, max_run_duration=%s",
            job_wrapper.get_id_tag(),
            cpu_milli,
            memory_mib,
            max_run_duration,
        )

        # Configure NFS volumes from gcp_batch_volumes parameter
        volumes_param = params.get("gcp_batch_volumes")
        parsed_volumes = parse_volumes_param(volumes_param) if volumes_param else []

        if parsed_volumes:
            batch_volumes = []
            for vol in parsed_volumes:
                volume = batch_v1.Volume()
                volume.nfs = batch_v1.NFS()
                volume.nfs.server = vol["server"]
                volume.nfs.remote_path = vol["remote_path"]
                volume.mount_path = vol["mount_path"]
                batch_volumes.append(volume)
                log.debug(
                    "Configured NFS volume: %s:%s -> %s for job %s",
                    vol["server"],
                    vol["remote_path"],
                    vol["mount_path"],
                    job_wrapper.get_id_tag(),
                )
            task_spec.volumes = batch_volumes

        # Create task group
        task_group = batch_v1.TaskGroup()
        task_group.task_count = 1
        task_group.task_spec = task_spec

        # Create allocation policy
        allocation_policy = batch_v1.AllocationPolicy()

        # Configure network for NFS access (required when using NFS volumes)
        if parsed_volumes:
            network_interface = batch_v1.AllocationPolicy.NetworkInterface()
            network_interface.network = f"global/networks/{params.get('network', 'default')}"
            network_interface.subnetwork = f"regions/{params['region']}/subnetworks/{params.get('subnet', 'default')}"

            network_policy = batch_v1.AllocationPolicy.NetworkPolicy()
            network_policy.network_interfaces = [network_interface]
            allocation_policy.network = network_policy
            log.debug(
                "Configured network for NFS access: %s/%s for job %s",
                params.get("network", "default"),
                params.get("subnet", "default"),
                job_wrapper.get_id_tag(),
            )

        # Configure instance
        instance_template = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
        instance_policy = batch_v1.AllocationPolicy.InstancePolicy()

        # Compute appropriate machine type based on resource requirements
        machine_type = compute_machine_type(cpu_milli, memory_mib)
        instance_policy.machine_type = machine_type
        log.debug(
            "Selected machine type %s for job %s (requested: %d mCPU, %d MiB)",
            machine_type,
            job_wrapper.get_id_tag(),
            cpu_milli,
            memory_mib,
        )

        # Use custom VM image if specified
        if params.get("custom_vm_image"):
            instance_policy.boot_disk = batch_v1.AllocationPolicy.Disk()
            instance_policy.boot_disk.image = params["custom_vm_image"]
            instance_policy.boot_disk.size_gb = params["boot_disk_size_gb"]
            instance_policy.boot_disk.type_ = params["boot_disk_type"]
            log.debug("Using custom VM image: %s for job %s", params["custom_vm_image"], job_wrapper.get_id_tag())
        else:
            # Configure standard boot disk
            disk = batch_v1.AllocationPolicy.Disk()
            disk.size_gb = params["boot_disk_size_gb"]
            disk.type_ = params["boot_disk_type"]
            instance_policy.boot_disk = disk

        instance_template.policy = instance_policy
        allocation_policy.instances = [instance_template]

        # Configure service account for job execution
        if service_account_email := params.get("service_account_email"):
            service_account = batch_v1.ServiceAccount()
            service_account.email = service_account_email
            allocation_policy.service_account = service_account
            log.debug("Configured service account: %s for job %s", service_account_email, job_wrapper.get_id_tag())
        else:
            log.warning(
                "No service account email specified for job %s - using default compute service account",
                job_wrapper.get_id_tag(),
            )

        # Create job
        job = batch_v1.Job()
        job.task_groups = [task_group]
        job.allocation_policy = allocation_policy

        # Configure logging
        job.logs_policy = batch_v1.LogsPolicy()
        job.logs_policy.destination = batch_v1.LogsPolicy.Destination.CLOUD_LOGGING  # type: ignore[assignment]

        # Set labels for tracking
        job.labels = {
            "galaxy-job-id": str(job_wrapper.job_id),
            "galaxy-tool-id": sanitize_label_value(job_wrapper.tool.id if job_wrapper.tool else "unknown"),
            "galaxy-runner": "gcp-batch",
            "galaxy-handler": sanitize_label_value(self.app.config.server_name),
        }

        log.debug("Finished _create_batch_job_spec for job %s", job_wrapper.get_id_tag())
        return job

    def _get_container_image(self, job_wrapper):
        """Get the container image using Galaxy's container finder.

        Uses Galaxy's standard container resolution system. Tools must have
        container requirements configured for containerized execution.
        """
        # Use Galaxy's container finder system (same approach as Kubernetes runner)
        container = self._find_container(job_wrapper)
        if container and hasattr(container, "container_id") and container.container_id:
            log.info(
                "Using tool-specific container: %s for job %s",
                container.container_id,
                job_wrapper.get_id_tag(),
            )
            return container.container_id

        raise ValueError(
            f"No container image found for job {job_wrapper.get_id_tag()}. "
            "Ensure tool has container requirements configured."
        )

    def _get_job_resources(self, job_wrapper, params):
        """
        Extract CPU and memory requirements from job wrapper and return as GCP Batch format.
        Returns tuple of (cpu_milli, memory_mib).
        """
        # Get job destination parameters
        job_destination = job_wrapper.job_destination

        # Get job resource parameters (from user selection in tool form)
        resource_params = job_wrapper.get_resource_parameters()

        # Determine CPU requirements (in milli-cores)
        cpu_milli = self._get_cpu_milli(job_destination, params, resource_params)

        # Determine memory requirements (in MiB)
        memory_mib = self._get_memory_mib(job_destination, params, resource_params)

        log.debug(
            "Job %s resource requirements: %.1f CPU cores (%d mCPU), %d MiB memory",
            job_wrapper.get_id_tag(),
            cpu_milli / 1000.0,
            cpu_milli,
            memory_mib,
        )

        return cpu_milli, memory_mib

    def _get_cpu_milli(self, job_destination, params, resource_params):
        """Get CPU requirements in milli-cores (1000 = 1 vCPU)."""
        # Check for job-specific CPU requests (highest priority)
        if "requests_cpu" in job_destination.params:
            cpu_str = job_destination.params["requests_cpu"]
            return convert_cpu_to_milli(cpu_str)

        # Check for job-specific CPU limits
        if "limits_cpu" in job_destination.params:
            cpu_str = job_destination.params["limits_cpu"]
            return convert_cpu_to_milli(cpu_str)

        # Check for Galaxy job resource parameter 'processors'
        if resource_params.get("processors"):
            cpu_str = str(resource_params["processors"])
            return convert_cpu_to_milli(cpu_str)

        # Check for TPV-style 'cores' in destination params
        if "cores" in job_destination.params:
            cpu_str = job_destination.params["cores"]
            return convert_cpu_to_milli(cpu_str)

        # Fall back to configured default
        default_vcpu = float(params.get("vcpu", 1.0))
        return int(default_vcpu * 1000)

    def _get_memory_mib(self, job_destination, params, resource_params):
        """Get memory requirements in MiB."""
        # Check for job-specific memory requests (highest priority)
        if "requests_memory" in job_destination.params:
            memory_str = job_destination.params["requests_memory"]
            return convert_memory_to_mib(memory_str)

        # Check for job-specific memory limits
        if "limits_memory" in job_destination.params:
            memory_str = job_destination.params["limits_memory"]
            return convert_memory_to_mib(memory_str)

        # Check for Galaxy job resource parameter 'mem' (in GB, convert to MiB)
        if resource_params.get("mem"):
            mem_gb = float(resource_params["mem"])
            return int(mem_gb * 1024)  # Convert GB to MiB

        # Check for TPV-style 'mem' in destination params (in GB)
        if "mem" in job_destination.params:
            mem_gb = float(job_destination.params["mem"])
            return int(mem_gb * 1024)  # Convert GB to MiB

        # Fall back to configured default
        return int(params.get("memory_mib", DEFAULT_MEMORY_MIB))

    def _container_script_settings(self, params, cpu_milli):
        """Resolve the settings shared by the container execution scripts.

        Used by both the non-pooled container script and the pooled task
        payload so the two render from identical NFS/docker settings.
        """
        # Parse volumes from gcp_batch_volumes parameter
        volumes_param = params.get("gcp_batch_volumes")
        parsed_volumes = parse_volumes_param(volumes_param) if volumes_param else []

        # Get the primary NFS volume (first one) for script template
        if parsed_volumes:
            primary_volume = parsed_volumes[0]
            nfs_server = primary_volume["server"]
            nfs_path = primary_volume["remote_path"]
            nfs_mount_path = primary_volume["mount_path"]
        else:
            # Fallback defaults if no volumes configured
            nfs_server = "127.0.0.1"
            nfs_path = DEFAULT_NFS_PATH
            nfs_mount_path = DEFAULT_NFS_MOUNT_PATH

        # Build Docker volume arguments from docker_extra_volumes parameter
        if docker_volumes_param := params.get("docker_extra_volumes"):
            docker_volume_args = parse_docker_volumes_param(docker_volumes_param)
        else:
            # Default to CVMFS mount if no extra volumes specified
            docker_volume_args = DEFAULT_CVMFS_DOCKER_VOLUME

        # Build docker user flag only if user/group IDs are configured
        user_id = params.get("galaxy_user_id")
        group_id = params.get("galaxy_group_id")
        if user_id and group_id:
            docker_user_flag = f"--user {user_id}:{group_id}"
        elif user_id:
            docker_user_flag = f"--user {user_id}"
        else:
            docker_user_flag = ""

        return {
            "nfs_server": nfs_server,
            "nfs_path": nfs_path,
            "nfs_mount_path": nfs_mount_path,
            "docker_volume_args": docker_volume_args,
            "docker_user_flag": docker_user_flag,
            # Compute galaxy_slots from allocated CPU (at least 1 slot)
            "galaxy_slots": max(1, int(cpu_milli / 1000)),
        }

    def _create_container_execution_script(self, job_wrapper, ajs, params, container_image, cpu_milli, memory_mib):
        """Create a script that runs the Galaxy job inside a container with volume mounts."""
        settings = self._container_script_settings(params, cpu_milli)

        template_params = {
            "job_id_tag": job_wrapper.get_id_tag(),
            "tool_id": job_wrapper.tool.id if job_wrapper.tool else "unknown",
            "container_image": container_image,
            "nfs_server": settings["nfs_server"],
            "nfs_path": settings["nfs_path"],
            "nfs_mount_path": settings["nfs_mount_path"],
            "job_file": ajs.job_file,
            "galaxy_slots": settings["galaxy_slots"],
            "galaxy_memory_mb": memory_mib,
            "docker_user_flag": settings["docker_user_flag"],
            "docker_volume_args": settings["docker_volume_args"],
        }

        return CONTAINER_SCRIPT_TEMPLATE.substitute(template_params)

    def _create_direct_execution_script(self, job_wrapper, ajs, params, cpu_milli, memory_mib):
        """Create a script that runs the Galaxy job directly on the VM (without container)."""
        # Parse volumes from gcp_batch_volumes parameter
        volumes_param = params.get("gcp_batch_volumes")
        parsed_volumes = parse_volumes_param(volumes_param) if volumes_param else []

        # Get the primary NFS mount path (first volume) for script template
        if parsed_volumes:
            nfs_mount_path = parsed_volumes[0]["mount_path"]
        else:
            nfs_mount_path = DEFAULT_NFS_MOUNT_PATH

        # Compute galaxy_slots from allocated CPU (at least 1 slot)
        galaxy_slots = max(1, int(cpu_milli / 1000))

        template_params = {
            "job_id_tag": job_wrapper.get_id_tag(),
            "tool_id": job_wrapper.tool.id if job_wrapper.tool else "unknown",
            "nfs_mount_path": nfs_mount_path,
            "job_file": ajs.job_file,
            "galaxy_slots": galaxy_slots,
            "galaxy_memory_mb": memory_mib,
        }

        return DIRECT_SCRIPT_TEMPLATE.substitute(template_params)

    def _write_debug_files(self, job_wrapper, ajs, params, request, job_name):
        """Write job parameters and request object to JSON files for debugging."""
        log.debug("Starting _write_debug_files for job %s", job_wrapper.get_id_tag())

        working_dir = ajs.job_wrapper.working_directory

        # Write job parameters to JSON
        params_file = os.path.join(working_dir, "gcp_batch_job_params.json")
        params_data = {
            "galaxy_job_id": job_wrapper.job_id,
            "galaxy_tool_id": job_wrapper.tool.id if job_wrapper.tool else None,
            "galaxy_tool_version": job_wrapper.tool.version if job_wrapper.tool else None,
            "batch_job_name": job_name,
            "timestamp": time.time(),
            "parameters": params,
            "runner_params": dict(self.runner_params),
            "job_destination": {
                "id": job_wrapper.job_destination.id,
                "tags": job_wrapper.job_destination.tags,
                "params": dict(job_wrapper.job_destination.params),
            },
        }

        with open(params_file, "w") as f:
            json.dump(params_data, f, indent=2, default=str)
        log.debug("Wrote job parameters to %s", params_file)

        # Convert the request object to a JSON-serializable format
        try:
            # Create a simplified representation of the request
            request_data = {
                "parent": request.parent,
                "job_id": request.job_id,
                "job": {"labels": dict(request.job.labels) if request.job.labels else {}, "task_groups": []},
            }

            # Add task group information
            for tg in request.job.task_groups:
                task_group_data = {
                    "task_count": tg.task_count,
                    "task_spec": {
                        "max_retry_count": tg.task_spec.max_retry_count,
                        "max_run_duration": tg.task_spec.max_run_duration,
                        "compute_resource": (
                            {
                                "cpu_milli": tg.task_spec.compute_resource.cpu_milli,
                                "memory_mib": tg.task_spec.compute_resource.memory_mib,
                            }
                            if tg.task_spec.compute_resource
                            else None
                        ),
                        "runnables": [],
                    },
                }

                # Add runnable information
                for runnable in tg.task_spec.runnables:
                    runnable_data = {}
                    if runnable.container:
                        runnable_data["container"] = {
                            "image_uri": runnable.container.image_uri,
                            "commands": list(runnable.container.commands) if runnable.container.commands else [],
                        }
                    task_group_data["task_spec"]["runnables"].append(runnable_data)

                request_data["job"]["task_groups"].append(task_group_data)

            # Add allocation policy
            if request.job.allocation_policy:
                ap = request.job.allocation_policy
                request_data["job"]["allocation_policy"] = {"instances": []}

                for instance in ap.instances:
                    instance_data = {}
                    if instance.policy:
                        instance_data["policy"] = {"machine_type": instance.policy.machine_type}
                        if instance.policy.boot_disk:
                            instance_data["policy"]["boot_disk"] = {
                                "size_gb": instance.policy.boot_disk.size_gb,
                                "type": instance.policy.boot_disk.type_,
                            }
                    request_data["job"]["allocation_policy"]["instances"].append(instance_data)

            # Write request object to JSON
            request_file = os.path.join(working_dir, "gcp_batch_job_request.json")
            with open(request_file, "w") as f:
                json.dump(request_data, f, indent=2, default=str)
            log.debug("Wrote job request to %s", request_file)

        except Exception as e:
            log.warning("Failed to serialize request object for job %s: %s", job_wrapper.get_id_tag(), e)
            # Write a minimal request file with basic info
            minimal_request = {
                "parent": request.parent,
                "job_id": request.job_id,
                "error": f"Failed to serialize full request: {e}",
            }
            request_file = os.path.join(working_dir, "gcp_batch_job_request.json")
            with open(request_file, "w") as f:
                json.dump(minimal_request, f, indent=2, default=str)
            log.debug("Wrote minimal job request to %s", request_file)

        log.debug("Finished _write_debug_files for job %s", job_wrapper.get_id_tag())

    def check_watched_items(self) -> None:
        """Run the standard per-job checks, then reap the warm VM pool.

        Runs every monitor_sleep_time seconds on the runner's monitor thread;
        no extra thread is needed for pool bookkeeping.
        """
        super().check_watched_items()
        try:
            self._reap_pool()
        except Exception:
            log.exception("Unhandled exception reaping the GCP Batch VM pool")

    def check_watched_item(self, job_state):
        """Check the status of a job running on Google Cloud Batch."""
        if getattr(job_state, "pooled_vm_id", None):
            return self._check_pooled_item(job_state)
        log.debug("Starting check_watched_item for job %s", job_state.job_id)

        batch_job_name = job_state.job_id
        try:
            # Get job status from Google Cloud Batch
            job_path = f"projects/{self.runner_params['project_id']}/locations/{self.runner_params['region']}/jobs/{batch_job_name}"
            batch_job = self.batch_client.get_job(name=job_path)

            # Process job status
            job_status = batch_job.status.state
            log.debug("Batch job %s status: %s", batch_job_name, job_status.name)

            if job_status == batch_v1.JobStatus.State.SUCCEEDED:
                log.info("Batch job %s completed successfully", batch_job_name)
                job_state.running = False
                job_state.job_wrapper.change_state(model.Job.states.OK)
                self.mark_as_finished(job_state)
                log.debug("Finished check_watched_item for job %s (completed successfully)", job_state.job_id)
                return None  # Remove from monitoring

            elif job_status == batch_v1.JobStatus.State.FAILED:
                log.warning("Batch job %s failed", batch_job_name)
                job_state.running = False
                job_state.job_wrapper.change_state(model.Job.states.ERROR)
                self.mark_as_failed(job_state)
                log.debug("Finished check_watched_item for job %s (failed)", job_state.job_id)
                return None  # Remove from monitoring

            elif job_status in [
                batch_v1.JobStatus.State.RUNNING,
                batch_v1.JobStatus.State.QUEUED,
                batch_v1.JobStatus.State.SCHEDULED,
            ]:
                log.debug("Batch job %s is %s", batch_job_name, job_status.name)
                job_state.running = True
                if job_status == batch_v1.JobStatus.State.RUNNING:
                    job_state.job_wrapper.change_state(model.Job.states.RUNNING)
                elif job_status in [batch_v1.JobStatus.State.SCHEDULED, batch_v1.JobStatus.State.QUEUED]:
                    job_state.job_wrapper.change_state(model.Job.states.QUEUED)
                log.debug("Finished check_watched_item for job %s (still running/queued/scheduled)", job_state.job_id)
                return job_state  # Continue monitoring

            else:
                log.warning("Batch job %s in unexpected state: %s", batch_job_name, job_status.name)
                return job_state  # Continue monitoring

        except gcp_exceptions.NotFound:
            log.error("Batch job %s not found", batch_job_name)
            job_state.running = False
            job_state.job_wrapper.change_state(model.Job.states.ERROR)
            self.mark_as_failed(job_state)
            return None

        except Exception as e:
            log.error("Error checking status of Batch job %s: %s", batch_job_name, e)
            # Return job_state to continue monitoring - might be temporary error
            return job_state

    def _vm_batch_job_path(self, vm_id: str) -> str:
        """Fully qualified Batch job path for a pooled VM.

        Prefers the path stored at submit time (which used destination-merged
        params); falls back to runner-level project/region only if the VM is
        somehow not registered.
        """
        if vm := self._vm_pool.get(vm_id):
            return vm.batch_job_path
        return f"projects/{self.runner_params['project_id']}/locations/{self.runner_params['region']}/jobs/{vm_id}"

    def _check_pooled_item(self, job_state):
        """Monitor a job handed to a pooled VM via the NFS handoff protocol.

        State machine (see util/gcp_batch/pool.py for the protocol):
          - done marker present        -> finished; VM back to the pool
          - task.sh still unclaimed    -> waiting; after the claim deadline the
                                          payload is reclaimed (atomic rename)
                                          and the job resubmitted fresh
          - claimed, no done marker    -> running; Batch job death or a stale
                                          heartbeat fails the Galaxy job
        """
        vm_id = job_state.pooled_vm_id
        vm_dir = job_state.pooled_vm_dir
        galaxy_job_id = str(job_state.job_wrapper.job_id)
        done_path = os.path.join(vm_dir, f"{DONE_PREFIX}{galaxy_job_id}")
        task_path = os.path.join(vm_dir, TASK_FILENAME)

        if os.path.exists(done_path):
            log.info("Pooled job %s finished on VM %s", job_state.job_wrapper.get_id_tag(), vm_id)
            job_state.running = False
            job_state.job_wrapper.change_state(model.Job.states.OK)
            self.mark_as_finished(job_state)
            try:
                os.remove(done_path)
            except OSError:
                pass
            self._return_vm_to_pool(job_state, vm_id)
            return None

        # Batch state doubles as the VM death detector on every branch below.
        try:
            batch_job = self.batch_client.get_job(name=self._vm_batch_job_path(vm_id))
            batch_state = batch_job.status.state
        except gcp_exceptions.NotFound:
            batch_state = None
        except Exception as e:
            log.error("Error checking status of pooled Batch job %s: %s", vm_id, e)
            return job_state  # might be a temporary error; keep watching

        vm_dead = batch_state in (None, batch_v1.JobStatus.State.FAILED, batch_v1.JobStatus.State.SUCCEEDED)

        if os.path.exists(task_path):
            # ASSIGNED: the payload has not been claimed yet.
            if vm_dead:
                # The VM died (or exited) before claiming; the job never
                # started, so resubmitting fresh is safe.
                log.warning("Pooled VM %s gone before claiming job %s; resubmitting fresh", vm_id, galaxy_job_id)
                return self._reclaim_and_resubmit(job_state, vm_id, task_path)
            deadline = getattr(job_state, "pool_claim_deadline", None)
            if deadline is None:
                # Fresh VM still provisioning; start the claim clock once the
                # Batch job reports RUNNING (the wrapper is then booting).
                if batch_state == batch_v1.JobStatus.State.RUNNING:
                    job_state.pool_claim_deadline = time.time() + job_state.pool_claim_timeout
                else:
                    job_state.job_wrapper.change_state(model.Job.states.QUEUED)
                return job_state
            if time.time() > deadline:
                log.warning("Pooled VM %s did not claim job %s in time; reclaiming", vm_id, galaxy_job_id)
                return self._reclaim_and_resubmit(job_state, vm_id, task_path)
            return job_state

        # CLAIMED: the job is (or was) running on the VM.
        if not vm_dead:
            heartbeat_path = os.path.join(vm_dir, HEARTBEAT_FILENAME)
            try:
                vm_dead = (time.time() - os.path.getmtime(heartbeat_path)) > HEARTBEAT_STALE_AFTER
            except OSError:
                pass  # heartbeat not written yet; Batch state governs
        if vm_dead:
            log.warning("Pooled VM %s died while running job %s", vm_id, galaxy_job_id)
            self._deregister_vm_by_id(vm_id)
            job_state.running = False
            job_state.fail_message = "The pooled VM running this job terminated unexpectedly"
            job_state.job_wrapper.change_state(model.Job.states.ERROR)
            self.mark_as_failed(job_state)
            return None
        job_state.running = True
        job_state.job_wrapper.change_state(model.Job.states.RUNNING)
        return job_state

    def _reclaim_and_resubmit(self, job_state, vm_id, task_path):
        """Reclaim an unclaimed payload and resubmit the job as a fresh Batch job.

        The reclaim is the runner's side of the rename race: if the mv fails
        because the wrapper claimed the file first, the job proceeds normally.
        """
        try:
            os.rename(task_path, f"{task_path}.reclaimed.{os.urandom(4).hex()}")
        except FileNotFoundError:
            # The VM won the race and claimed the payload just now.
            return job_state
        except OSError as e:
            log.error("Failed to reclaim payload for job %s from VM %s: %s", job_state.job_id, vm_id, e)
            return job_state
        self._deregister_vm_by_id(vm_id)
        job_state.pooled_vm_id = None
        job_state.pooled_vm_dir = None
        job_state.pool_claim_deadline = None
        # Resubmission happens on a worker thread, not the monitor thread.
        self.work_queue.put((self._fallback_submit_fresh, job_state))
        return None

    def _fallback_submit_fresh(self, ajs):
        """Resubmit a job whose pooled handoff was reclaimed as a fresh Batch job."""
        job_wrapper = ajs.job_wrapper
        job = job_wrapper.get_job()
        if job.state in (
            model.Job.states.DELETING,
            model.Job.states.DELETED,
            model.Job.states.STOPPING,
            model.Job.states.STOPPED,
        ):
            # The user cancelled the job while its handoff was pending; do not
            # spend money running it fresh.
            log.info("Not resubmitting job %s after reclaim; job state is %s", job_wrapper.get_id_tag(), job.state)
            return
        try:
            batch_job_name = self._submit_batch_job(job_wrapper, ajs)
            ajs.job_id = batch_job_name
            job_wrapper.set_external_id(batch_job_name)
            self.monitor_queue.put(ajs)
            log.info(
                "Resubmitted job %s as fresh Batch job %s after unclaimed pooled handoff",
                job_wrapper.get_id_tag(),
                batch_job_name,
            )
        except Exception as e:
            log.error("Failed to resubmit job %s to Google Cloud Batch: %s", job_wrapper.get_id_tag(), e)
            job_wrapper.fail(f"Failed to submit job to Google Cloud Batch: {e}")

    def _return_vm_to_pool(self, job_state, vm_id):
        """Return a VM whose job completed to the idle pool, or drain it."""
        vm = self._vm_pool.get(vm_id)
        if vm is None:
            return
        now = time.time()
        max_size = getattr(job_state, "pool_max_size", 0)
        if self._vm_pool.idle_count(vm.pool_key) >= max_size:
            log.debug("Idle pool at capacity (%d); draining VM %s", max_size, vm_id)
            self._drain_vm(vm)
        elif vm.remaining_lifetime(now) < vm.idle_ttl_seconds + POOL_SAFETY_MARGIN:
            log.debug("VM %s near its lifetime deadline; draining", vm_id)
            self._drain_vm(vm)
        else:
            self._vm_pool.checkin(vm_id, now)
            log.debug("Returned VM %s to the warm pool", vm_id)

    def _drain_vm(self, vm):
        """Ask a pooled VM to exit (shutdown marker) and mark it draining."""
        try:
            atomic_write(os.path.join(vm.vm_dir, SHUTDOWN_FILENAME), "")
        except OSError as e:
            log.warning("Failed to write shutdown marker for pooled VM %s: %s", vm.batch_job_name, e)
        self._vm_pool.mark_draining(vm.batch_job_name, time.time())

    def _deregister_vm_by_id(self, vm_id, delete_batch_job=True):
        vm = self._vm_pool.remove(vm_id)
        if vm is None:
            return
        if delete_batch_job and self.runner_params.get("delete_completed_jobs", True):
            try:
                self.batch_client.delete_job(name=vm.batch_job_path)
                log.debug("Deleted pooled Batch job %s", vm.batch_job_name)
            except gcp_exceptions.NotFound:
                pass
            except Exception as e:
                log.warning("Failed to delete pooled Batch job %s: %s", vm.batch_job_name, e)
        shutil.rmtree(vm.vm_dir, ignore_errors=True)

    def _reap_pool(self):
        """Pool housekeeping, run once per monitor sweep.

        Idle VMs past their TTL get a shutdown marker; draining VMs that
        ignored it get their Batch job deleted; VMs whose Batch job is terminal
        (or that announced they are exiting) are deregistered and cleaned up.
        BUSY/PROVISIONING VMs are owned by their job's monitor entry.
        """
        now = time.time()
        for vm in self._vm_pool.expired_idle(now):
            log.debug("Pooled VM %s idle past its TTL; sending shutdown", vm.batch_job_name)
            self._drain_vm(vm)
        for vm in self._vm_pool.all_vms():
            if vm.state not in (VMState.IDLE, VMState.DRAINING):
                continue
            try:
                batch_job = self.batch_client.get_job(name=vm.batch_job_path)
                terminal = batch_job.status.state in (
                    batch_v1.JobStatus.State.SUCCEEDED,
                    batch_v1.JobStatus.State.FAILED,
                )
            except gcp_exceptions.NotFound:
                terminal = True
            except Exception as e:
                log.warning("Error checking pooled Batch job %s: %s", vm.batch_job_name, e)
                continue
            if terminal or os.path.exists(os.path.join(vm.vm_dir, EXITING_FILENAME)):
                self._deregister_vm_by_id(vm.batch_job_name)
            elif (
                vm.state is VMState.DRAINING
                and vm.idle_since is not None
                and (now - vm.idle_since) > POOL_SAFETY_MARGIN
            ):
                # The VM ignored the shutdown marker (e.g. NFS trouble); force it.
                log.warning("Pooled VM %s ignored shutdown; deleting its Batch job", vm.batch_job_name)
                self._deregister_vm_by_id(vm.batch_job_name)

    def stop_job(self, job_wrapper):
        """Stop a job running on Google Cloud Batch."""
        job = job_wrapper.get_job()
        log.debug("Starting stop_job for job %s", job.id)

        if batch_job_name := job.get_job_runner_external_id():
            if self._stop_pooled_job(job, batch_job_name):
                log.debug("Finished stop_job for pooled job %s", job.id)
                return
            if not self.runner_params.get("delete_completed_jobs", True):
                try:
                    job_path = f"projects/{self.runner_params['project_id']}/locations/{self.runner_params['region']}/jobs/{batch_job_name}"
                    batch_job = self.batch_client.get_job(name=job_path)
                    if batch_job.status.state in (
                        batch_v1.JobStatus.State.SUCCEEDED,
                        batch_v1.JobStatus.State.FAILED,
                    ):
                        log.info("Retaining completed Batch job %s (delete_completed_jobs=false)", batch_job_name)
                        return
                except gcp_exceptions.NotFound:
                    log.debug("Batch job %s already deleted", batch_job_name)
                    return
                except Exception as e:
                    log.warning("Failed to check Batch job %s status, proceeding with delete: %s", batch_job_name, e)

            try:
                job_path = f"projects/{self.runner_params['project_id']}/locations/{self.runner_params['region']}/jobs/{batch_job_name}"
                self.batch_client.delete_job(name=job_path)
                log.info("Deleted Batch job %s", batch_job_name)
            except gcp_exceptions.NotFound:
                log.debug("Batch job %s already deleted", batch_job_name)
            except Exception as e:
                log.error("Failed to delete Batch job %s: %s", batch_job_name, e)
        else:
            log.warning("Could not stop job %s - no external job ID", job.id)

        log.debug("Finished stop_job for job %s", job.id)

    def _stop_pooled_job(self, job, batch_job_name) -> bool:
        """Pooled-job branch of stop_job. Returns True if the stop was handled.

        The cancel marker makes the wrapper kill the named container; the
        shutdown marker makes the VM exit (it is not returned to the pool).
        The Batch job delete is the backstop in case the NFS markers never
        reach the VM.
        """
        vm = self._vm_pool.get(batch_job_name)
        if vm is None:
            return False
        if vm.current_galaxy_job_id != str(job.id):
            # The VM has already moved on (idle or running another job); the
            # Batch job must NOT be deleted out from under it.
            log.debug("Pooled VM %s no longer running job %s; nothing to stop", batch_job_name, job.id)
            return True
        try:
            atomic_write(os.path.join(vm.vm_dir, CANCEL_FILENAME), "")
            atomic_write(os.path.join(vm.vm_dir, SHUTDOWN_FILENAME), "")
        except OSError as e:
            log.warning("Failed to write cancel markers for pooled VM %s: %s", batch_job_name, e)
        self._vm_pool.mark_draining(batch_job_name, time.time())
        try:
            self.batch_client.delete_job(name=vm.batch_job_path)
            log.info("Deleted pooled Batch job %s to stop job %s", batch_job_name, job.id)
        except gcp_exceptions.NotFound:
            log.debug("Pooled Batch job %s already deleted", batch_job_name)
        except Exception as e:
            log.error("Failed to delete pooled Batch job %s: %s", batch_job_name, e)
        return True

    def shutdown(self):
        """Shut down the runner, tearing down idle pooled VMs.

        BUSY VMs are left running: their jobs are recovered on restart and the
        wrapper's own TTL/lifetime deadlines guarantee eventual teardown even
        if Galaxy never comes back.
        """
        super().shutdown()
        pool = getattr(self, "_vm_pool", None)
        if pool is None:
            return
        for vm in pool.all_vms():
            if vm.state not in (VMState.IDLE, VMState.DRAINING):
                continue
            try:
                atomic_write(os.path.join(vm.vm_dir, SHUTDOWN_FILENAME), "")
            except OSError:
                pass
            try:
                self.batch_client.delete_job(name=vm.batch_job_path)
                log.info("Deleted idle pooled Batch job %s on shutdown", vm.batch_job_name)
            except Exception as e:
                log.debug("Failed to delete idle pooled Batch job %s on shutdown: %s", vm.batch_job_name, e)
            pool.remove(vm.batch_job_name)

    def recover(self, job, job_wrapper):
        """Recover jobs that were running when Galaxy restarted."""
        log.debug("Starting recover for job %s", job.id)
        log.info("Recovering job %s", job.id)

        # Create AsynchronousJobState for recovery
        ajs = AsynchronousJobState(
            files_dir=job_wrapper.working_directory,
            job_wrapper=job_wrapper,
            job_destination=job_wrapper.job_destination,
        )

        # Set the external job ID
        ajs.job_id = job.get_job_runner_external_id()

        if ajs.job_id:
            self._maybe_reattach_pooled(job, job_wrapper, ajs)
            # Add to monitoring if job was running
            if job.state in [model.Job.states.RUNNING, model.Job.states.QUEUED]:
                ajs.running = job.state == model.Job.states.RUNNING
                self.monitor_queue.put(ajs)
                log.info("Recovered job %s for monitoring", job.id)
        else:
            log.warning("Could not recover job %s - no external job ID", job.id)

        log.debug("Finished recover for job %s", job.id)

    def _maybe_reattach_pooled(self, job, job_wrapper, ajs) -> None:
        """Reattach a recovered job to its pooled VM if its handoff dir exists.

        _check_pooled_item then handles every case: the done marker is already
        there, the job is still running, or the VM died meanwhile. Idle VMs
        lost from memory on restart self-expire via the wrapper's TTL, so they
        need no rediscovery.
        """
        params = self._get_job_params(job_wrapper.job_destination)
        if not string_as_bool(params.get("pool_enabled") or False):
            return
        vm_dir = os.path.join(self._resolve_pool_dir(params), ajs.job_id)
        if not os.path.isdir(vm_dir):
            return
        now = time.time()
        if self._vm_pool.get(ajs.job_id) is None:
            cpu_milli, memory_mib = self._get_job_resources(job_wrapper, params)
            machine_type = compute_machine_type(cpu_milli, memory_mib)
            self._vm_pool.register(
                PooledVM(
                    batch_job_name=ajs.job_id,
                    pool_key=compute_pool_key(self._pool_key_params(params, machine_type)),
                    vm_dir=vm_dir,
                    batch_job_path=f"projects/{params['project_id']}/locations/{params['region']}/jobs/{ajs.job_id}",
                    # created_at is unknown after a restart; stamping now
                    # overstates the remaining lifetime, which is safe because
                    # VM death detection covers an earlier-than-expected exit.
                    created_at=now,
                    lifetime_seconds=int(convert_duration_to_seconds(params["pool_max_vm_lifetime"]).rstrip("s")),
                    idle_ttl_seconds=int(params["pool_ttl_seconds"]),
                    state=VMState.BUSY,
                    current_galaxy_job_id=str(job.id),
                )
            )
        self._set_pooled_state(
            ajs, params, ajs.job_id, vm_dir, claim_deadline=now + int(params["pool_claim_timeout_seconds"])
        )
        log.info("Reattached recovered job %s to pooled VM %s", job.id, ajs.job_id)
