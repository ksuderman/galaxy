"""
Google Cloud Batch Job Runner with Embedded Pulsar Integration
Extends the existing Google Batch runner with Pulsar staging capabilities
"""

import logging
import os
import tempfile
from typing import Optional, Dict, Any

try:
    from pulsar.core import PulsarApp
    from pulsar.client import build_client_manager, ClientManager
    from pulsar.client.staging import stage_in, stage_out
except ImportError:
    PulsarApp = None
    build_client_manager = None
    ClientManager = None
    stage_in = None
    stage_out = None

from galaxy.jobs.runners.gcp import GoogleBatchJobRunner
from galaxy.jobs.runners.pulsar import PulsarJobRunner, PULSAR_PARAM_SPECS
from galaxy.jobs import JobDestination
from galaxy.util import specs

log = logging.getLogger(__name__)


class GCPBatchPulsarJobRunner(GoogleBatchJobRunner, PulsarJobRunner):
    """
    Google Cloud Batch job runner with embedded Pulsar staging
    Combines GCP Batch execution with Pulsar file staging capabilities
    """

    runner_name = "GCPBatchPulsarJobRunner"
    
    # Parameter specifications for the combined runner
    PULSAR_PARAM_SPECS = {
        # Google Batch parameters
        'project_id': dict(map=str, default=None),
        'region': dict(map=str, default='us-central1'),
        'machine_type': dict(map=str, default='e2-standard-4'),
        'boot_disk_size_gb': dict(map=int, default=10),
        'container_image': dict(map=str, default='galaxyproject/galaxy-minimal:latest'),
        'use_workload_identity': dict(map=specs.to_bool, default=False),
        
        # Pulsar staging parameters
        'pulsar_embedded_config': dict(map=dict, default={}),
        'shared_storage': dict(map=dict, default={}),
        'staging_directory': dict(map=str, default='/tmp/pulsar-staging'),
        'galaxy_url': dict(map=str, default='http://localhost:8080'),
        'private_token': dict(map=str, default=''),
        
        # Standard Pulsar parameters (from parent class)
        'transport': dict(map=specs.to_str_or_none, valid=specs.is_in("urllib", "curl", None), default=None),
        'cache': dict(map=specs.to_bool_or_none, default=None),
        'default_file_action': dict(map=str, default='copy'),
        'dependency_resolution': dict(map=str, default='remote'),
        'rewrite_parameters': dict(map=specs.to_bool, default=True),
    }

    def __init__(self, app, nworkers, **kwargs):
        # Check if Pulsar libraries are available
        if PulsarApp is None:
            raise Exception("pulsar-app library is required for GCPBatchPulsarJobRunner")
            
        # Initialize Google Batch runner first
        super().__init__(app, nworkers, **kwargs)

        # Initialize Pulsar components
        self.pulsar_app = None
        self.pulsar_client_manager = None
        
        # Validate required parameters
        self._validate_runner_params()
        
        # Initialize Pulsar configuration
        self._init_pulsar_config()
        
    def _validate_runner_params(self):
        """Validate required runner parameters"""
        required_params = ['project_id']
        
        for param in required_params:
            if not self.runner_params.get(param):
                if param == 'project_id':
                    # Try to get from environment or config
                    try:
                        self.runner_params[param] = self._get_project_id()
                    except Exception:
                        raise ValueError(f"Required parameter '{param}' not found in runner configuration")
                else:
                    raise ValueError(f"Required parameter '{param}' not found in runner configuration")
        
        # Validate shared storage if using staging
        shared_storage = self.runner_params.get('shared_storage', {})
        if shared_storage:
            if not shared_storage.get('bucket'):
                raise ValueError("shared_storage.bucket is required when using Pulsar staging")
                
        log.info("Runner parameters validated successfully")

    def _init_pulsar_config(self):
        """Initialize embedded Pulsar configuration"""
        pulsar_config = self.runner_params.get('pulsar_embedded_config', {})
        
        # Set default values
        pulsar_config.setdefault('staging_directory', self.runner_params.get('staging_directory', '/tmp/pulsar-staging'))
        pulsar_config.setdefault('galaxy_url', self.runner_params.get('galaxy_url', 'http://localhost:8080'))
        pulsar_config.setdefault('private_token', self.runner_params.get('private_token', ''))
        
        # Ensure staging directory exists
        staging_dir = pulsar_config['staging_directory']
        os.makedirs(staging_dir, exist_ok=True)
        
        # Set up embedded Pulsar server
        self._setup_embedded_pulsar(pulsar_config)
        
    def _setup_embedded_pulsar(self, pulsar_config):
        """Initialize the embedded Pulsar server and client manager"""
        try:
            # Create Pulsar app configuration
            app_config = {
                'staging_directory': pulsar_config['staging_directory'],
                'galaxy_url': pulsar_config['galaxy_url'],
                'private_token': pulsar_config['private_token'],
                'tool_dependency_dir': 'none',
                'conda_auto_init': False,
                'conda_auto_install': False,
                'galaxy_infrastructure_url': pulsar_config['galaxy_url'],
            }
            
            # Initialize Pulsar app
            self.pulsar_app = PulsarApp(**app_config)
            log.info("Initialized embedded Pulsar app")
            
            # Create client manager for staging operations
            client_config = {
                'url': f"embedded:{pulsar_config['staging_directory']}",
                'private_token': pulsar_config['private_token'],
            }
            
            self.pulsar_client_manager = build_client_manager(**client_config)
            log.info("Initialized Pulsar client manager for staging")
            
        except Exception as e:
            log.error(f"Failed to initialize embedded Pulsar: {e}")
            raise

    def queue_job(self, job_wrapper):
        """
        Queue job with Pulsar staging to Google Batch
        """
        # Stage files using Pulsar
        self._stage_files_with_pulsar(job_wrapper)

        # Submit to Google Batch with staged file locations
        super().queue_job(job_wrapper)

    def _stage_files_with_pulsar(self, job_wrapper):
        """
        Stage job files using embedded Pulsar to GCS
        """
        try:
            # Get shared storage configuration
            shared_storage = self.runner_params.get('shared_storage', {})
            if not shared_storage:
                log.warning("No shared storage configured, skipping Pulsar staging")
                return
                
            # Create staging directories
            staging_dir = self.pulsar_app.manager.staging_directory if self.pulsar_app else '/tmp/pulsar-staging'
            job_staging_dir = os.path.join(staging_dir, str(job_wrapper.job_id))
            os.makedirs(job_staging_dir, exist_ok=True)
            
            # Stage input files
            self._stage_input_files(job_wrapper, job_staging_dir, shared_storage)
            
            # Update job wrapper with staged file locations
            self._update_job_paths_for_staging(job_wrapper, job_staging_dir, shared_storage)
            
            log.info(f"Successfully staged files for job {job_wrapper.job_id} to {job_staging_dir}")
            
        except Exception as e:
            log.error(f"Failed to stage files for job {job_wrapper.job_id}: {e}")
            raise
            
    def _stage_input_files(self, job_wrapper, job_staging_dir, shared_storage):
        """Stage input datasets to the staging directory"""
        
        # Create input directory structure
        inputs_dir = os.path.join(job_staging_dir, 'inputs')
        os.makedirs(inputs_dir, exist_ok=True)
        
        # Stage each input dataset
        for dataset_path in job_wrapper.get_input_paths():
            if os.path.exists(dataset_path):
                # Copy file to staging directory
                filename = os.path.basename(dataset_path)
                staged_path = os.path.join(inputs_dir, filename)
                
                # Use hard link if on same filesystem, otherwise copy
                try:
                    os.link(dataset_path, staged_path)
                    log.debug(f"Hard linked {dataset_path} to {staged_path}")
                except OSError:
                    # Fallback to copy if hard link fails
                    import shutil
                    shutil.copy2(dataset_path, staged_path)
                    log.debug(f"Copied {dataset_path} to {staged_path}")
                    
        # Stage tool files and working directory content
        working_dir = job_wrapper.working_directory
        if os.path.exists(working_dir):
            work_staging_dir = os.path.join(job_staging_dir, 'work')
            os.makedirs(work_staging_dir, exist_ok=True)
            
            # Copy essential working directory files
            for item in os.listdir(working_dir):
                src_path = os.path.join(working_dir, item)
                if os.path.isfile(src_path):
                    dst_path = os.path.join(work_staging_dir, item)
                    import shutil
                    shutil.copy2(src_path, dst_path)
                    
    def _update_job_paths_for_staging(self, job_wrapper, job_staging_dir, shared_storage):
        """Update job wrapper paths to point to staged locations"""
        
        # Update working directory to point to staged location
        bucket = shared_storage.get('bucket', '')
        mount_path = shared_storage.get('mount_path', '/mnt/galaxy-data')
        
        # The staged files will be available in the container at the mount path
        container_staging_dir = os.path.join(mount_path, 'staging', str(job_wrapper.job_id))
        
        # Store original paths for later restoration
        job_wrapper._original_working_directory = job_wrapper.working_directory
        job_wrapper._container_staging_dir = container_staging_dir
        job_wrapper._local_staging_dir = job_staging_dir
        
        log.debug(f"Job {job_wrapper.job_id} will use container staging dir: {container_staging_dir}")

    def _create_batch_job(self, job_wrapper, job_params):
        """
        Override to include Pulsar-staged file locations in the batch job
        """
        # Get the base job from parent class
        batch_job = super()._create_batch_job(job_wrapper, job_params)
        
        # Add GCS volume mount for staged files if shared storage is configured
        shared_storage = self.runner_params.get('shared_storage', {})
        if shared_storage and shared_storage.get('bucket'):
            bucket = shared_storage['bucket']
            mount_path = shared_storage.get('mount_path', '/mnt/galaxy-data')
            
            # Add volume mount to the task spec
            if not hasattr(batch_job.task_groups[0].task_spec.runnables[0], 'volumes'):
                batch_job.task_groups[0].task_spec.runnables[0].volumes = []
                
            # Add GCS bucket mount
            from google.cloud import batch_v1
            volume = batch_v1.Volume()
            volume.gcs = batch_v1.GCS()
            volume.gcs.remote_path = f"gs://{bucket}"
            volume.mount_path = mount_path
            volume.mount_options = ["rw"]
            
            batch_job.task_groups[0].task_spec.runnables[0].volumes.append(volume)
            
            log.debug(f"Added GCS volume mount: gs://{bucket} -> {mount_path}")
            
        return batch_job
        
    def _prepare_job_command(self, job_wrapper):
        """
        Prepare the job command with Pulsar staging paths
        """
        # Update command to use staged file locations if available
        if hasattr(job_wrapper, '_container_staging_dir'):
            # Modify job command to work with staged files
            original_command = job_wrapper.get_command_line()
            
            # Replace working directory references with container staging directory
            container_work_dir = os.path.join(job_wrapper._container_staging_dir, 'work')
            modified_command = original_command.replace(
                job_wrapper._original_working_directory,
                container_work_dir
            )
            
            log.debug(f"Modified job command for staging: {modified_command}")
            return modified_command
        
        return job_wrapper.get_command_line()
        
    def finish_job(self, job_state):
        """
        Complete job processing with output staging
        """
        try:
            # Stage outputs back from GCS if needed
            self._stage_outputs_from_gcs(job_state.job_wrapper)
            
            # Call parent finish_job
            super().finish_job(job_state)
            
        except Exception as e:
            log.error(f"Failed to finish job {job_state.job_id}: {e}")
            job_state.job_wrapper.fail(f"Failed to stage outputs: {e}")
            
    def _stage_outputs_from_gcs(self, job_wrapper):
        """
        Stage job outputs back from GCS to local Galaxy storage
        """
        if not hasattr(job_wrapper, '_local_staging_dir'):
            # No staging was used
            return
            
        try:
            local_staging_dir = job_wrapper._local_staging_dir
            original_working_dir = job_wrapper._original_working_directory
            
            # Copy outputs from staging back to original working directory
            outputs_dir = os.path.join(local_staging_dir, 'outputs')
            if os.path.exists(outputs_dir):
                import shutil
                for item in os.listdir(outputs_dir):
                    src_path = os.path.join(outputs_dir, item)
                    dst_path = os.path.join(original_working_dir, item)
                    
                    if os.path.isfile(src_path):
                        shutil.copy2(src_path, dst_path)
                        log.debug(f"Staged output {src_path} to {dst_path}")
                        
            # Clean up staging directory
            if os.path.exists(local_staging_dir):
                shutil.rmtree(local_staging_dir)
                log.debug(f"Cleaned up staging directory {local_staging_dir}")
                
        except Exception as e:
            log.error(f"Failed to stage outputs for job {job_wrapper.job_id}: {e}")
            # Don't raise - let job continue, outputs might still be accessible
            
    def check_watched_item(self, job_state):
        """
        Monitor job state with enhanced logging for staging
        """
        try:
            # Call parent monitoring
            result = super().check_watched_item(job_state)
            
            # Add staging-specific monitoring
            if hasattr(job_state.job_wrapper, '_local_staging_dir'):
                staging_dir = job_state.job_wrapper._local_staging_dir
                if os.path.exists(staging_dir):
                    log.debug(f"Job {job_state.job_id} staging directory exists: {staging_dir}")
                    
            return result
            
        except Exception as e:
            log.error(f"Error monitoring job {job_state.job_id}: {e}")
            return None