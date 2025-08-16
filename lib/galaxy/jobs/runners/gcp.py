"""
Galaxy Job Runner for Google Cloud Batch

This module provides a job runner that dispatches Galaxy jobs to Google Cloud Batch.
"""

import logging
import os
import time
from typing import Dict, Any

from galaxy.jobs.runners import AsynchronousJobState, AsynchronousJobRunner
from galaxy.jobs import JobDestination

try:
    from google.cloud import batch_v1
    from google.auth import default
    from google.api_core import exceptions as gcp_exceptions
except ImportError:
    batch_v1 = None
    default = None
    gcp_exceptions = None

log = logging.getLogger(__name__)

__all__ = ['GoogleBatchJobRunner']


class GoogleBatchJobRunner(AsynchronousJobRunner):
    """
    Job runner for Google Cloud Batch service.

    This runner submits Galaxy jobs to Google Cloud Batch for execution
    in containerized environments on Google Cloud Platform.
    """

    runner_name = "GoogleBatchJobRunner"

    PULSAR_PARAM_SPECS = {
        'pulsar_embedded_config': dict(map=dict, default={}),
        'shared_storage': dict(map=dict, default={}),
        'use_workload_identity': dict(map=specs.to_bool, default=False),
    }

    def __init__(self, app, nworkers, **kwargs):
        """Initialize the Google Batch job runner."""
        super().__init__(app, nworkers, **kwargs)

        # Check if Google Cloud libraries are available
        if batch_v1 is None:
            raise Exception("google-cloud-batch library is required for GoogleBatchJobRunner")

        # Initialize Google Cloud Batch client
        self._init_batch_client()

        # Configuration
        self.default_project_id = self._get_project_id()
        self.default_region = self._get_config_value('region', 'us-central1')
        self.default_machine_type = self._get_config_value('machine_type', 'e2-standard-4')
        self.default_boot_disk_size_gb = int(self._get_config_value('boot_disk_size_gb', '10'))
        self.default_container_image = self._get_config_value('container_image', 'galaxyproject/galaxy-minimal:latest')

        # Job monitoring
        self._job_states = {}  # job_id -> batch job name mapping

        log.info(f"GoogleBatchJobRunner initialized for project: {self.default_project_id}")

    def _init_batch_client(self):
        """Initialize the Google Cloud Batch client."""
        try:
            credentials, project = default()
            self.batch_client = batch_v1.BatchServiceClient(credentials=credentials)
            self._project_from_auth = project
        except Exception as e:
            log.error(f"Failed to initialize Google Cloud Batch client: {e}")
            raise

    def _get_project_id(self):
        """Get the Google Cloud project ID."""
        # Try from environment variable first
        project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')
        if project_id:
            return project_id

        # Try from auth
        if hasattr(self, '_project_from_auth') and self._project_from_auth:
            return self._project_from_auth

        # Try from config
        project_id = self._get_config_value('project_id')
        if project_id:
            return project_id

        raise Exception(
            "Google Cloud project ID not found. Set GOOGLE_CLOUD_PROJECT environment variable or configure project_id.")

    def _get_config_value(self, key: str, default: str = None) -> str:
        """Get configuration value from Galaxy config."""
        config_key = f'google_batch_{key}'
        return getattr(self.app.config, config_key, default)

    def queue_job(self, job_wrapper):
        """Queue a job for execution on Google Batch."""
        try:
            job_destination = job_wrapper.job_destination

            # Extract job parameters
            job_params = self._prepare_job_params(job_wrapper, job_destination)

            # Create Batch job
            batch_job = self._create_batch_job(job_wrapper, job_params)

            # Submit job to Google Batch
            batch_job_name = self._submit_job(batch_job, job_params)

            # Create asynchronous job state
            ajs = AsynchronousJobState(
                files_dir=job_wrapper.working_directory,
                job_wrapper=job_wrapper,
                job_id=batch_job_name,
                runner=self
            )

            # Track job state
            self._job_states[job_wrapper.job_id] = batch_job_name

            # Add to monitor queue
            self.monitor_queue.put(ajs)

            log.info(f"Queued job {job_wrapper.job_id} as Batch job {batch_job_name}")

        except Exception as e:
            log.error(f"Failed to queue job {job_wrapper.job_id}: {e}")
            job_wrapper.fail(f"Failed to submit job to Google Batch: {e}")

    def _prepare_job_params(self, job_wrapper, job_destination: JobDestination) -> Dict[str, Any]:
        """Prepare job parameters from job destination and wrapper."""
        params = {
            'project_id': job_destination.params.get('project_id', self.default_project_id),
            'region': job_destination.params.get('region', self.default_region),
            'machine_type': job_destination.params.get('machine_type', self.default_machine_type),
            'boot_disk_size_gb': int(job_destination.params.get('boot_disk_size_gb', self.default_boot_disk_size_gb)),
            'container_image': job_destination.params.get('container_image', self.default_container_image),
            'cpu_count': int(job_destination.params.get('cpu_count', '1')),
            'memory_gb': int(job_destination.params.get('memory_gb', '4')),
            'max_retry_count': int(job_destination.params.get('max_retry_count', '3')),
            'max_run_duration': job_destination.params.get('max_run_duration', '3600s'),
        }

        # Add environment variables
        env_vars = {}
        if hasattr(job_wrapper, 'environment_variables'):
            env_vars.update(job_wrapper.environment_variables)

        # Add Galaxy-specific environment variables
        env_vars.update({
            'GALAXY_JOB_ID': str(job_wrapper.job_id),
            'GALAXY_TOOL_ID': job_wrapper.tool.id if job_wrapper.tool else '',
        })

        params['environment_variables'] = env_vars

        return params

    def _create_batch_job(self, job_wrapper, job_params: Dict[str, Any]) -> batch_v1.Job:
        """Create a Google Batch job specification."""

        # Create task specification
        task_spec = batch_v1.TaskSpec()

        # Configure container runnable
        container = batch_v1.Runnable.Container()
        container.image_uri = job_params['container_image']
        container.commands = ['/bin/bash']
        container.entrypoint = ''

        # Add command to execute Galaxy job
        job_script_path = job_wrapper.get_command_line()
        container.options = f'-c "{job_script_path}"'

        # Set environment variables
        for key, value in job_params['environment_variables'].items():
            container.env_vars[key] = str(value)

        # Create runnable
        runnable = batch_v1.Runnable()
        runnable.container = container

        task_spec.runnables = [runnable]
        task_spec.max_retry_count = job_params['max_retry_count']
        task_spec.max_run_duration = job_params['max_run_duration']

        # Create task group
        task_group = batch_v1.TaskGroup()
        task_group.task_count = 1
        task_group.task_spec = task_spec

        # Create allocation policy
        allocation_policy = batch_v1.AllocationPolicy()

        # Set compute resources
        compute_resource = batch_v1.ComputeResource()
        compute_resource.cpu_milli = job_params['cpu_count'] * 1000  # Convert to milliCPU
        compute_resource.memory_mib = job_params['memory_gb'] * 1024  # Convert GB to MiB
        task_spec.compute_resource = compute_resource

        # Configure instance
        instance_template = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
        instance_policy = batch_v1.AllocationPolicy.InstancePolicy()
        # instance_policy.machine_type = job_params['machine_type']

        # Configure boot disk
        disk = batch_v1.AllocationPolicy.Disk()
        disk.size_gb = job_params['boot_disk_size_gb']
        disk.type_ = 'pd-standard'
        instance_policy.boot_disk = disk

        instance_template.policy = instance_policy
        allocation_policy.instances = [instance_template]

        # Create job
        job = batch_v1.Job()
        job.task_groups = [task_group]
        job.allocation_policy = allocation_policy

        # Set labels for tracking
        job.labels = {
            'galaxy-job-id': str(job_wrapper.job_id),
            'galaxy-tool-id': job_wrapper.tool.id.replace('_', '-') if job_wrapper.tool else 'unknown',
            'galaxy-runner': 'google-batch'
        }

        return job

    def _submit_job(self, batch_job: batch_v1.Job, job_params: Dict[str, Any]) -> str:
        """Submit job to Google Batch and return job name."""

        # Generate unique job name
        job_name = f"galaxy-job-{int(time.time())}-{os.urandom(4).hex()}"

        # Create request
        request = batch_v1.CreateJobRequest()
        request.parent = f"projects/{job_params['project_id']}/locations/{job_params['region']}"
        request.job_id = job_name
        request.job = batch_job

        # Submit job
        try:
            operation = self.batch_client.create_job(request=request)
            log.info(f"Submitted Batch job: {job_name}")
            return job_name
        except gcp_exceptions.GoogleAPIError as e:
            log.error(f"Failed to submit Batch job: {e}")
            raise

    def check_watched_item(self, job_state: AsynchronousJobState):
        """Check the status of a job running on Google Batch."""
        batch_job_name = job_state.job_id
        log.debug("Checking status of Batch job %s", batch_job_name)
        try:
            # Get job status
            job_path = f"projects/{self.default_project_id}/locations/{self.default_region}/jobs/{batch_job_name}"
            job = self.batch_client.get_job(name=job_path)

            # Map Batch job state to Galaxy job state
            if job.status.state == batch_v1.JobStatus.State.SUCCEEDED:
                log.info(f"Batch job {batch_job_name} completed successfully")
                job_state.job_wrapper.change_state(job_state.job_wrapper.states.OK)
                job_state.running = False
                self.mark_as_finished(job_state)
                job_state = None

            elif job.status.state == batch_v1.JobStatus.State.FAILED:
                log.warning(f"Batch job {batch_job_name} failed")
                job_state.job_wrapper.fail("Job failed on Google Batch")
                job_state.running = False
                self.mark_as_failed(job_state)
                job_state = None

            elif job.status.state == batch_v1.JobStatus.State.RUNNING:
                # Job is still running or queued
                log.debug("Batch job %s is %s", batch_job_name, job.status.state.name)
                job_state.running = True
            elif job.status.state == batch_v1.JobStatus.State.QUEUED:
                log.debug("Batch job %s is queued", batch_job_name)
                job_state.running = False
                job_state.job_wrapper.change_state(job_state.job_wrapper.states.QUEUED)
                self.mark_as_queued(job_state)
            else:
                log.warning("Batch job %s in unexpected state: %s", batch_job_name, job.status.state )

        except gcp_exceptions.NotFound:
            log.error(f"Batch job {batch_job_name} not found")
            job_state.job_wrapper.fail("Job not found on Google Batch")
            job_state.running = False

        except Exception as e:
            log.error(f"Error checking job {batch_job_name}: {e}")
            # Don't fail the job on temporary errors, will retry
        return job_state

    def stop_job(self, job_wrapper):
        """Stop/cancel a job running on Google Batch."""
        log.debug("Stopping job %s", job_wrapper.job_id)
        if job_wrapper.job_id in self._job_states:
            batch_job_name = self._job_states[job_wrapper.job_id]

            try:
                job_path = f"projects/{self.default_project_id}/locations/{self.default_region}/jobs/{batch_job_name}"
                self.batch_client.delete_job(name=job_path)
                log.info("Cancelled Batch job %s", batch_job_name)

            except Exception as e:
                log.error(f"Failed to cancel Batch job {batch_job_name}: {e}")

            finally:
                # Clean up tracking
                del self._job_states[job_wrapper.job_id]

    def recover(self, job, job_wrapper):
        """Recover a job after Galaxy restart."""
        log.info(f"Recovering job {job.id}")
        # Implementation depends on how job state is persisted
        # This is a placeholder for recovery logic
        pass