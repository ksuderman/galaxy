# Google Batch + Embedded Pulsar Integration for Galaxy

This document outlines the configuration changes and code modifications needed to enable Google Batch job dispatching through Galaxy's embedded Pulsar server.

## Overview

This implementation combines:
- **Google Cloud Batch** for scalable job execution
- **Embedded Pulsar** for file staging and job orchestration
- **Google Cloud Storage** for shared file system access
- **Container-based execution** for tool isolation

The solution leverages existing Galaxy infrastructure patterns from the AWS Batch runner and embedded Pulsar implementations.

## 1. Configuration Changes

### 1.1 job_conf.yml

Add the following configuration to your `job_conf.yml`:

```yaml
runners:
  # Enhanced Google Batch Runner with Pulsar Integration
  google_batch_pulsar:
    load: galaxy.jobs.runners.gcp_batch_pulsar:GCPBatchPulsarJobRunner
    
    # Google Cloud Configuration
    project_id: your-gcp-project-id
    region: us-central1
    zone: us-central1-a
    
    # Service Account Authentication
    service_account_file: /path/to/service-account-key.json
    # OR use workload identity (recommended for K8s deployments)
    use_workload_identity: true
    
    # Google Batch Job Configuration
    machine_type: e2-standard-4
    boot_disk_size_gb: 100
    boot_disk_type: pd-standard
    
    # Container Configuration
    docker_enabled: true
    docker_default_container_id: gcr.io/your-project/galaxy-tools:latest
    
    # Shared Storage Configuration
    shared_storage:
      type: gcs  # Google Cloud Storage
      bucket: your-galaxy-storage-bucket
      mount_path: /mnt/galaxy-data
      
    # Embedded Pulsar Configuration
    pulsar_embedded_config:
      staging_directory: /tmp/pulsar-staging
      galaxy_url: http://your-galaxy-instance:8080
      private_token: your-pulsar-private-token
      conda_auto_init: false
      conda_auto_install: false
      tool_dependency_dir: none

execution:
  environments:
    google_batch_pulsar:
      runner: google_batch_pulsar
      
      # Resource specifications
      google_batch_cpu: 4
      google_batch_memory: 16  # GB
      google_batch_disk: 100   # GB
      
      # Container settings
      docker_enabled: true
      singularity_enabled: false
      
      # File staging
      default_file_action: remote_transfer
      remote_metadata: true
      
      # Job retry settings
      max_retries: 3
      retry_delay_base: 30  # seconds

tools:
  # Route compute-intensive tools to Google Batch
  - class: local
    environment: local
  - id: bwa
    environment: google_batch_pulsar
  - id: hisat2
    environment: google_batch_pulsar
```

### 1.2 galaxy.yml

Add these sections to your `galaxy.yml`:

```yaml
# Google Cloud Integration
google_batch:
  enabled: true
  project_id: your-gcp-project-id
  default_region: us-central1
  service_account_file: /path/to/service-account-key.json

# Object Store Configuration (for shared storage)
object_store_config_file: config/object_store_conf.yml

# File Sources (for GCS integration) 
file_sources_config_file: config/file_sources_conf.yml
```

### 1.3 object_store_conf.yml (New File)

Create `config/object_store_conf.yml`:

```yaml
type: cloud
provider: google
auth:
  credentials_file: /path/to/service-account-key.json

bucket:
  name: your-galaxy-storage-bucket
  use_reduced_redundancy: false

cache:
  path: database/object_store_cache
  size: 10000

extra_dirs:
- type: job_work
  path: database/job_working_directory_gcp
- type: temp
  path: database/tmp_gcp
```

### 1.4 file_sources_conf.yml Updates

Add to your `config/file_sources_conf.yml`:

```yaml
- type: googlecloudstorage
  id: galaxy_gcs_batch
  label: Galaxy GCS Batch Storage
  bucket: your-galaxy-storage-bucket
  service_account_file: /path/to/service-account-key.json
  writable: true
```

## 2. Code Changes

### 2.1 Create New Job Runner

Create `lib/galaxy/jobs/runners/gcp_batch_pulsar.py`:

```python
"""
Google Cloud Batch Job Runner with Embedded Pulsar Integration
Extends the existing Google Batch runner with Pulsar staging capabilities
"""

import logging
import os
from typing import Optional

from galaxy.jobs.runners.gcp import GCPBatchJobRunner
from galaxy.jobs.runners.pulsar import PulsarEmbeddedJobRunner, PulsarJobRunner
from galaxy.jobs import JobDestination
from galaxy.util import specs

log = logging.getLogger(__name__)

class GCPBatchPulsarJobRunner(GCPBatchJobRunner, PulsarJobRunner):
    """
    Google Cloud Batch job runner with embedded Pulsar staging
    Combines GCP Batch execution with Pulsar file staging capabilities
    """
    
    runner_name = "GCPBatchPulsarJobRunner"
    
    def __init__(self, app, nworkers, **kwargs):
        # Initialize both parent classes
        super().__init__(app, nworkers, **kwargs)
        
        # Initialize Pulsar components
        self._init_pulsar_config()
        
    def _init_pulsar_config(self):
        """Initialize embedded Pulsar configuration"""
        pulsar_config = self.runner_params.get('pulsar_embedded_config', {})
        
        # Set up embedded Pulsar server
        self._setup_embedded_pulsar(pulsar_config)
        
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
        # Implementation details for Pulsar staging
        pass
        
    def _setup_batch_job_spec(self, job_wrapper):
        """
        Override to include Pulsar-staged file locations
        """
        job_spec = super()._setup_batch_job_spec(job_wrapper)
        
        # Add GCS mount points for staged files
        job_spec['task_spec']['environment']['volumes'] = [{
            'gcs': {
                'remote_path': f"gs://{self.runner_params['shared_storage']['bucket']}/staging"
            },
            'mount_path': '/mnt/pulsar-staging'
        }]
        
        return job_spec
```

### 2.2 Extend Existing GCP Batch Runner (Alternative)

If using the existing `gcp-batch` branch runner, add these methods to `lib/galaxy/jobs/runners/gcp.py`:

```python
# Add to existing GCPBatchJobRunner class

PULSAR_PARAM_SPECS = {
    'pulsar_embedded_config': dict(map=dict, default={}),
    'shared_storage': dict(map=dict, default={}),
    'use_workload_identity': dict(map=specs.to_bool, default=False),
}

def _init_pulsar_integration(self):
    """Initialize Pulsar integration if configured"""
    if 'pulsar_embedded_config' in self.runner_params:
        from galaxy.jobs.runners.pulsar import PulsarEmbeddedJobRunner
        # Set up embedded Pulsar components
        pass

def _stage_inputs_to_gcs(self, job_wrapper):
    """Stage job inputs to Google Cloud Storage using Pulsar"""
    # Implementation for staging files
    pass

def _retrieve_outputs_from_gcs(self, job_wrapper):
    """Retrieve job outputs from Google Cloud Storage using Pulsar"""  
    # Implementation for retrieving outputs
    pass
```

### 2.3 Update Dependencies

The following dependencies have been added to `lib/galaxy/dependencies/conditional-requirements.txt`:

```txt
# Google Batch API dependencies
google-cloud-batch>=0.17.0  # type: gcp_batch_pulsar
google-cloud-storage>=2.8.0  # type: gcp_batch_pulsar
google-auth>=2.0.0  # type: gcp_batch_pulsar

# Google Cloud Platform cloudbridge provider dependencies (required for object store)
google-api-python-client>=2.0.0  # type: cloud_gcp
google-auth>=2.0.0  # type: cloud_gcp
google-auth-httplib2>=0.2.0  # type: cloud_gcp
google-cloud-core>=2.0.0  # type: cloud_gcp
```

## 3. Authentication Setup

### 3.1 Service Account Configuration

Create a Google Cloud service account with these IAM roles:
- `batch.jobsEditor`
- `storage.objectAdmin`
- `iam.serviceAccountUser`

Download the service account key and reference it in your configuration files.

### 3.2 Workload Identity Setup (for Kubernetes deployments)

```bash
# Create Kubernetes service account
kubectl create serviceaccount galaxy-gcp-batch

# Bind to Google service account
gcloud iam service-accounts add-iam-policy-binding \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:PROJECT_ID.svc.id.goog[NAMESPACE/galaxy-gcp-batch]" \
  GSA_NAME@PROJECT_ID.iam.gserviceaccount.com

kubectl annotate serviceaccount galaxy-gcp-batch \
  iam.gke.io/gcp-service-account=GSA_NAME@PROJECT_ID.iam.gserviceaccount.com
```

## 4. Deployment Steps

### 4.1 Install Dependencies

```bash
pip install google-cloud-batch google-cloud-storage google-auth
```

### 4.2 Set up Google Cloud Resources

```bash
# Create storage bucket
gsutil mb gs://your-galaxy-storage-bucket

# Enable required APIs
gcloud services enable batch.googleapis.com
gcloud services enable storage-api.googleapis.com
```

### 4.3 Configure Galaxy

1. Update configuration files as shown above
2. Restart Galaxy services
3. Verify job runner is loaded in Galaxy logs

### 4.4 Test Configuration

```bash
# Submit a test job to verify Google Batch integration
python scripts/grt/grt.py test_google_batch_pulsar
```

## 5. Architecture Benefits

This implementation provides:

1. **File staging via Pulsar** - Handles efficient data transfer between Galaxy and Google Batch containers
2. **Container-based execution** - Leverages Google Batch's managed container runtime
3. **Shared storage via GCS** - Provides scalable, performant data access patterns
4. **Authentication integration** - Supports both service accounts and workload identity
5. **Scalable job execution** - Takes advantage of Google Cloud's auto-scaling batch computing

## 6. Implementation Notes

This design leverages existing Galaxy patterns:
- **Existing Google Batch runner** from the `gcp-batch` branch as foundation
- **Embedded Pulsar patterns** from `lib/galaxy/jobs/runners/pulsar.py:1092-1109`
- **AWS Batch integration patterns** from `lib/galaxy/jobs/runners/aws.py`
- **Google Cloud Storage integration** from `lib/galaxy/files/sources/googlecloudstorage.py`

The modular approach allows for incremental implementation and testing while building on proven Galaxy infrastructure patterns.

## 7. Troubleshooting

### 7.1 CloudBridge GCP Provider Error

If you encounter the error:
```
NotImplementedError: A provider with name gcp could not be found
```

This indicates that the CloudBridge GCP provider dependencies are missing. Install them:

```bash
# Activate Galaxy virtual environment
source .venv/bin/activate

# Install required dependencies
pip install google-api-python-client google-auth google-auth-httplib2 google-cloud-core
```

The CloudBridge library includes GCP provider support but requires these additional Google Cloud libraries to function properly.

### 7.2 Verifying GCP Provider Installation

Test CloudBridge GCP provider functionality:

```bash
source .venv/bin/activate
python -c "
from cloudbridge.factory import CloudProviderFactory, ProviderList
config = {'gcp_service_creds_file': '/path/to/service-account.json'}
provider = CloudProviderFactory().create_provider(ProviderList.GCP, config)
print('SUCCESS: GCP provider created')
print('Provider:', provider.name)
"
```

### 7.3 Service Account Permissions

Ensure your Google Cloud service account has the following IAM roles:
- `batch.jobsEditor` - Required for Google Batch job management
- `storage.objectAdmin` - Required for Google Cloud Storage access
- `iam.serviceAccountUser` - Required for impersonation if needed

### 7.4 Configuration Validation

Verify your configurations are correct:
- Check that credential files exist and are readable
- Ensure GCS bucket names are globally unique and accessible
- Verify Google Cloud APIs are enabled in your project

## 8. Next Steps

1. Implement the core `GCPBatchPulsarJobRunner` class
2. Add comprehensive file staging logic between Galaxy, Pulsar, and GCS
3. Implement job monitoring and state management
4. Add comprehensive testing and documentation
5. Consider integration with Galaxy's dynamic job routing for automatic scaling