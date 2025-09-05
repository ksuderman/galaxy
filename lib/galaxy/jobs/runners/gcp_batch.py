"""
Google Cloud Batch Job Runner

This runner submits Galaxy jobs to Google Cloud Batch for execution.
"""

import json
import logging
import os
import time
from typing import (
    Any,
    Dict,
    Optional,
)

from google.api_core import exceptions as gcp_exceptions
from google.auth import default
from google.cloud import batch_v1

from galaxy import model
from galaxy.jobs.runners import (
    AsynchronousJobRunner,
    AsynchronousJobState,
)
from galaxy.util import asbool

log = logging.getLogger(__name__)

__all__ = ("GoogleCloudBatchJobRunner",)


class GoogleCloudBatchJobRunner(AsynchronousJobRunner):
    """
    Job runner that submits jobs to Google Cloud Batch.
    """

    runner_name = "GoogleCloudBatchJobRunner"

    def __init__(self, app, nworkers, **kwargs):
        """Initialize the Google Cloud Batch job runner."""
        log.trace("Starting GoogleCloudBatchJobRunner.__init__")

        # Define runner parameter specifications
        runner_param_specs = {
            "project_id": dict(map=str, default=None),
            "region": dict(map=str, default="us-central1"),
            "zone": dict(map=str, default=None),
            "service_account_file": dict(map=str, default=None),
            "machine_type": dict(map=str, default="e2-standard-4"),
            "boot_disk_size_gb": dict(map=int, default=100),
            "boot_disk_type": dict(map=str, default="pd-standard"),
            "container_image": dict(map=str, default="ubuntu:20.04"),
            "max_retry_count": dict(map=int, default=3),
            "max_run_duration": dict(map=str, default="3600s"),
            "polling_interval": dict(map=int, default=30),
        }

        kwargs.update({"runner_param_specs": runner_param_specs})
        super().__init__(app, nworkers, **kwargs)

        # Initialize Google Cloud Batch client
        self._init_batch_client()

        # Job tracking
        self._job_states = {}  # job_id -> batch job name mapping

        log.info(
            "GoogleCloudBatchJobRunner initialized for project: %s",
            self.runner_params.get("project_id", "Not specified"),
        )
        log.trace("Finished GoogleCloudBatchJobRunner.__init__")

    def _init_batch_client(self):
        """Initialize the Google Cloud Batch client."""
        log.trace("Starting _init_batch_client")
        try:
            # Set up authentication
            service_account_file = self.runner_params.get("service_account_file")
            if service_account_file:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = service_account_file

            credentials, project = default()
            self.batch_client = batch_v1.BatchServiceClient(credentials=credentials)

            # Set project ID
            if not self.runner_params.get("project_id"):
                if project:
                    self.runner_params["project_id"] = project
                else:
                    raise ValueError(
                        "Google Cloud project ID not specified and could not be determined from credentials"
                    )

            log.info("Google Cloud Batch client initialized successfully")
            log.trace("Finished _init_batch_client successfully")

        except ImportError as e:
            raise Exception("google-cloud-batch library is required for GoogleCloudBatchJobRunner") from e
        except Exception as e:
            log.error("Failed to initialize Google Cloud Batch client: %s", e)
            log.trace("Finished _init_batch_client with error")
            raise

    def queue_job(self, job_wrapper):
        """Queue a job for execution on Google Cloud Batch."""
        log.trace("Starting queue_job for Galaxy job %s", job_wrapper.get_id_tag())

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
            # Submit job to Google Cloud Batch
            batch_job_name = self._submit_batch_job(job_wrapper, ajs)

            # Store job state and add to monitoring queue
            ajs.job_id = batch_job_name
            self._job_states[job_wrapper.job_id] = batch_job_name
            self.monitor_queue.put(ajs)

            log.info("Successfully queued Galaxy job %s as Batch job %s", job_wrapper.get_id_tag(), batch_job_name)

        except Exception as e:
            log.error("Failed to submit job %s to Google Cloud Batch: %s", job_wrapper.get_id_tag(), e)
            job_wrapper.fail(f"Failed to submit job to Google Cloud Batch: {e}")

        log.trace("Finished queue_job for Galaxy job %s", job_wrapper.get_id_tag())

    def _submit_batch_job(self, job_wrapper, ajs) -> str:
        """Submit a job to Google Cloud Batch and return the job name."""
        log.trace("Starting _submit_batch_job for job %s", job_wrapper.get_id_tag())

        # Generate unique job name
        job_name = f"galaxy-job-{int(time.time())}-{os.urandom(4).hex()}"

        # Get job destination parameters
        job_destination = job_wrapper.job_destination
        params = self._get_job_params(job_destination)

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
            log.trace("Finished _submit_batch_job for job %s", job_wrapper.get_id_tag())
            return job_name
        except Exception as e:
            log.error("Failed to create Batch job: %s", e)
            log.trace("Finished _submit_batch_job for job %s with error", job_wrapper.get_id_tag())
            raise

    def _get_job_params(self, job_destination) -> Dict[str, Any]:
        """Extract job parameters from destination and runner configuration."""
        log.trace("Starting _get_job_params")
        params = {}

        # Copy from runner params with destination overrides
        for key in [
            "project_id",
            "region",
            "zone",
            "machine_type",
            "boot_disk_size_gb",
            "boot_disk_type",
            "container_image",
            "max_retry_count",
            "max_run_duration",
        ]:
            params[key] = job_destination.params.get(key, self.runner_params.get(key))

        log.trace("Finished _get_job_params")
        return params

    def _create_batch_job_spec(self, job_wrapper, ajs, params):
        """Create a Google Cloud Batch job specification."""
        log.trace("Starting _create_batch_job_spec for job %s", job_wrapper.get_id_tag())

        # Create task specification
        task_spec = batch_v1.TaskSpec()

        # Create container specification
        container = batch_v1.Runnable.Container()
        container.image_uri = params["container_image"]
        container.commands = ["/bin/bash", ajs.job_file]

        # Create runnable
        runnable = batch_v1.Runnable()
        runnable.container = container

        task_spec.runnables = [runnable]
        task_spec.max_retry_count = params["max_retry_count"]
        task_spec.max_run_duration = params["max_run_duration"]

        # Set compute resources
        compute_resource = batch_v1.ComputeResource()
        compute_resource.cpu_milli = 1000  # 1 CPU
        compute_resource.memory_mib = 4096  # 4GB
        task_spec.compute_resource = compute_resource

        # Create task group
        task_group = batch_v1.TaskGroup()
        task_group.task_count = 1
        task_group.task_spec = task_spec

        # Create allocation policy
        allocation_policy = batch_v1.AllocationPolicy()

        # Configure instance
        instance_template = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
        instance_policy = batch_v1.AllocationPolicy.InstancePolicy()
        instance_policy.machine_type = params["machine_type"]

        # Configure boot disk
        disk = batch_v1.AllocationPolicy.Disk()
        disk.size_gb = params["boot_disk_size_gb"]
        disk.type_ = params["boot_disk_type"]
        instance_policy.boot_disk = disk

        instance_template.policy = instance_policy
        allocation_policy.instances = [instance_template]

        # Create job
        job = batch_v1.Job()
        job.task_groups = [task_group]
        job.allocation_policy = allocation_policy

        # Set labels for tracking
        def _sanitize_label_value(value, max_length=63):
            """Sanitize a value to be used as a GCP label value."""
            if not value:
                return "unknown"

            # Convert to lowercase and replace invalid characters with dashes
            sanitized = "".join(c.lower() if c.isalnum() else "-" for c in str(value))

            # Remove consecutive dashes
            while "--" in sanitized:
                sanitized = sanitized.replace("--", "-")

            # Ensure it starts and ends with alphanumeric characters
            sanitized = sanitized.strip("-")
            if not sanitized:
                return "unknown"

            # Truncate if too long
            if len(sanitized) > max_length:
                sanitized = sanitized[:max_length].rstrip("-")

            # Ensure it's not empty after truncation
            return sanitized if sanitized else "unknown"

        job.labels = {
            "galaxy-job-id": str(job_wrapper.job_id),
            "galaxy-tool-id": _sanitize_label_value(job_wrapper.tool.id if job_wrapper.tool else "unknown"),
            "galaxy-runner": "gcp-batch",
        }

        log.trace("Finished _create_batch_job_spec for job %s", job_wrapper.get_id_tag())
        return job

    def _write_debug_files(self, job_wrapper, ajs, params, request, job_name):
        """Write job parameters and request object to JSON files for debugging."""
        log.trace("Starting _write_debug_files for job %s", job_wrapper.get_id_tag())

        working_dir = ajs.files_dir

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

        log.trace("Finished _write_debug_files for job %s", job_wrapper.get_id_tag())

    def check_watched_item(self, job_state):
        """Check the status of a job running on Google Cloud Batch."""
        log.trace("Starting check_watched_item for job %s", job_state.job_id)

        batch_job_name = job_state.job_id
        log.debug("Checking status of Batch job %s", batch_job_name)

        try:
            # Get job status from Google Cloud Batch
            job_path = f"projects/{self.runner_params['project_id']}/locations/{self.runner_params['region']}/jobs/{batch_job_name}"
            batch_job = self.batch_client.get_job(name=job_path)

            # Process job status
            job_status = batch_job.status.state

            if job_status == batch_v1.JobStatus.State.SUCCEEDED:
                log.info("Batch job %s completed successfully", batch_job_name)
                job_state.running = False
                job_state.job_wrapper.change_state(model.Job.states.OK)
                self.mark_as_finished(job_state)
                log.trace("Finished check_watched_item for job %s (completed successfully)", job_state.job_id)
                return None  # Remove from monitoring

            elif job_status == batch_v1.JobStatus.State.FAILED:
                log.warning("Batch job %s failed", batch_job_name)
                job_state.running = False
                job_state.job_wrapper.change_state(model.Job.states.ERROR)
                self.mark_as_failed(job_state)
                log.trace("Finished check_watched_item for job %s (failed)", job_state.job_id)
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
                log.trace("Finished check_watched_item for job %s (still running/queued/scheduled)", job_state.job_id)
                return job_state  # Continue monitoring

            else:
                log.warning("Batch job %s in unexpected state: %s", batch_job_name, job_status.name)
                log.trace("Finished check_watched_item for job %s (unexpected state)", job_state.job_id)
                return job_state  # Continue monitoring

        except gcp_exceptions.NotFound:
            log.error("Batch job %s not found", batch_job_name)
            job_state.running = False
            job_state.job_wrapper.change_state(model.Job.states.ERROR)
            self.mark_as_failed(job_state)
            log.trace("Finished check_watched_item for job %s (not found)", job_state.job_id)
            return None

        except Exception as e:
            log.error("Error checking status of Batch job %s: %s", batch_job_name, e)
            # Return job_state to continue monitoring - might be temporary error
            log.trace("Finished check_watched_item for job %s (continuing monitoring due to error)", job_state.job_id)
            return job_state

    def stop_job(self, job_wrapper):
        """Stop a job running on Google Cloud Batch."""
        job = job_wrapper.get_job()
        log.trace("Starting stop_job for job %s", job.id)

        if job.id in self._job_states:
            batch_job_name = self._job_states[job.id]

            try:
                job_path = f"projects/{self.runner_params['project_id']}/locations/{self.runner_params['region']}/jobs/{batch_job_name}"
                self.batch_client.delete_job(name=job_path)
                log.info("Cancelled Batch job %s", batch_job_name)

            except Exception as e:
                log.error("Failed to cancel Batch job %s: %s", batch_job_name, e)

            finally:
                # Clean up tracking
                del self._job_states[job.id]

        log.trace("Finished stop_job for job %s", job.id)

    def recover(self, job, job_wrapper):
        """Recover jobs that were running when Galaxy restarted."""
        log.trace("Starting recover for job %s", job.id)
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
            # Add to monitoring if job was running
            if job.state in [model.Job.states.RUNNING, model.Job.states.QUEUED]:
                ajs.running = job.state == model.Job.states.RUNNING
                self.monitor_queue.put(ajs)
                log.info("Recovered job %s for monitoring", job.id)
        else:
            log.warning("Could not recover job %s - no external job ID", job.id)

        log.trace("Finished recover for job %s", job.id)
