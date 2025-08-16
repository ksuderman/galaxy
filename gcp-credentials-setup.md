# Google Cloud Credentials Setup for Galaxy GCP Batch Pulsar Runner

This guide walks through setting up Google Cloud authentication for the GCP Batch Pulsar job runner.

## Prerequisites

1. **Google Cloud Project**: You need an active GCP project with billing enabled
2. **gcloud CLI**: Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
3. **Admin Access**: You need IAM admin permissions to create service accounts

## Step 1: Enable Required APIs

First, enable the necessary Google Cloud APIs for your project:

```bash
# Set your project ID
export PROJECT_ID="your-galaxy-project-id"
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable batch.googleapis.com
gcloud services enable storage-api.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable iam.googleapis.com
```

## Step 2: Create Service Account

Create a dedicated service account for Galaxy with the minimum required permissions:

```bash
# Create service account
gcloud iam service-accounts create galaxy-gcp-batch \
  --display-name="Galaxy GCP Batch Runner" \
  --description="Service account for Galaxy GCP Batch job execution"

# Get the service account email
export SA_EMAIL="galaxy-gcp-batch@${PROJECT_ID}.iam.gserviceaccount.com"
echo "Service account created: $SA_EMAIL"
```

## Step 3: Assign Required IAM Roles

Grant the service account the minimum permissions needed:

```bash
# Batch job management permissions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/batch.jobsEditor"

# Storage permissions for file staging
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.objectAdmin"

# Compute permissions for VM management
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/compute.instanceAdmin.v1"

# Service account user (for impersonation if needed)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/iam.serviceAccountUser"

# Logging permissions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/logging.logWriter"
```

## Step 4: Create and Download Service Account Key

```bash
# Create key file
gcloud iam service-accounts keys create galaxy-gcp-service-account.json \
  --iam-account=$SA_EMAIL

# Secure the key file
chmod 600 galaxy-gcp-service-account.json

# Move to secure location
sudo mkdir -p /etc/galaxy/credentials
sudo mv galaxy-gcp-service-account.json /etc/galaxy/credentials/
sudo chown galaxy:galaxy /etc/galaxy/credentials/galaxy-gcp-service-account.json
sudo chmod 400 /etc/galaxy/credentials/galaxy-gcp-service-account.json

echo "Service account key saved to: /etc/galaxy/credentials/galaxy-gcp-service-account.json"
```

## Step 5: Create GCS Bucket for File Staging

```bash
# Create bucket for Galaxy data staging
export BUCKET_NAME="galaxy-batch-staging-${PROJECT_ID}"
gsutil mb gs://$BUCKET_NAME

# Set bucket permissions
gsutil iam ch serviceAccount:$SA_EMAIL:objectAdmin gs://$BUCKET_NAME

echo "Created staging bucket: gs://$BUCKET_NAME"
```

## Step 6: Configure Galaxy

Update your Galaxy configuration files:

### 6.1 Update job_conf.yml

```yaml
runners:
  google_batch_pulsar:
    load: galaxy.jobs.runners.gcp_batch_pulsar:GCPBatchPulsarJobRunner
    
    # Google Cloud Configuration
    project_id: your-galaxy-project-id
    region: us-central1
    zone: us-central1-a
    
    # Service Account Authentication
    service_account_file: /etc/galaxy/credentials/galaxy-gcp-service-account.json
    
    # Google Batch Job Configuration
    machine_type: e2-standard-4
    boot_disk_size_gb: 100
    boot_disk_type: pd-standard
    
    # Container Configuration
    docker_enabled: true
    docker_default_container_id: galaxyproject/galaxy-minimal:latest
    
    # Shared Storage Configuration
    shared_storage:
      type: gcs
      bucket: galaxy-batch-staging-your-project-id
      mount_path: /mnt/galaxy-data
      
    # Embedded Pulsar Configuration
    pulsar_embedded_config:
      staging_directory: /tmp/pulsar-staging
      galaxy_url: http://localhost:8080
      private_token: your-secure-random-token
      conda_auto_init: false
      conda_auto_install: false
      tool_dependency_dir: none

execution:
  environments:
    google_batch_pulsar:
      runner: google_batch_pulsar
      google_batch_cpu: 4
      google_batch_memory: 16
      google_batch_disk: 100
      docker_enabled: true
      default_file_action: remote_transfer
      remote_metadata: true
      max_retries: 3

tools:
  - class: local
    environment: local
  - id: bwa
    environment: google_batch_pulsar
  - id: hisat2  
    environment: google_batch_pulsar
```

### 6.2 Update galaxy.yml

```yaml
# Google Cloud Integration
google_batch:
  enabled: true
  project_id: your-galaxy-project-id
  default_region: us-central1
  service_account_file: /etc/galaxy/credentials/galaxy-gcp-service-account.json

# Object Store Configuration
object_store_config_file: config/object_store_conf.yml

# File Sources Configuration  
file_sources_config_file: config/file_sources_conf.yml
```

### 6.3 Create object_store_conf.yml

```yaml
type: cloud
provider: google
auth:
  credentials_file: /etc/galaxy/credentials/galaxy-gcp-service-account.json

bucket:
  name: galaxy-batch-staging-your-project-id
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

### 6.4 Update file_sources_conf.yml

```yaml
- type: googlecloudstorage
  id: galaxy_gcs_batch
  label: Galaxy GCS Batch Storage
  bucket: galaxy-batch-staging-your-project-id
  service_account_file: /etc/galaxy/credentials/galaxy-gcp-service-account.json
  writable: true
```

## Step 7: Set Environment Variables

Add these to your Galaxy startup script or systemd service:

```bash
# In galaxy startup script or .bashrc
export GOOGLE_APPLICATION_CREDENTIALS="/etc/galaxy/credentials/galaxy-gcp-service-account.json"
export GOOGLE_CLOUD_PROJECT="your-galaxy-project-id"
```

## Step 8: Test Authentication

Create a test script to verify authentication works:

```bash
cat > test_gcp_auth.py << 'EOF'
#!/usr/bin/env python3
import os
from google.auth import default
from google.cloud import batch_v1
from google.cloud import storage

def test_auth():
    try:
        # Test default authentication
        credentials, project = default()
        print(f"✓ Authentication successful for project: {project}")
        
        # Test Batch API access
        batch_client = batch_v1.BatchServiceClient(credentials=credentials)
        print("✓ Batch API client created successfully")
        
        # Test Storage API access
        storage_client = storage.Client(credentials=credentials, project=project)
        print("✓ Storage API client created successfully")
        
        # List buckets to verify permissions
        buckets = list(storage_client.list_buckets())
        print(f"✓ Can access {len(buckets)} storage buckets")
        
        return True
        
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return False

if __name__ == "__main__":
    success = test_auth()
    exit(0 if success else 1)
EOF

# Run the test
python3 test_gcp_auth.py
```

## Alternative: Workload Identity for Kubernetes

If running Galaxy in Google Kubernetes Engine (GKE), use Workload Identity instead of service account keys:

### Enable Workload Identity

```bash
# Enable Workload Identity on cluster
gcloud container clusters update CLUSTER_NAME \
  --region=REGION \
  --workload-pool=$PROJECT_ID.svc.id.goog

# Enable on node pool
gcloud container node-pools update NODEPOOL_NAME \
  --cluster=CLUSTER_NAME \
  --region=REGION \
  --workload-metadata=GKE_METADATA
```

### Configure Workload Identity

```bash
# Create Kubernetes service account
kubectl create serviceaccount galaxy-gcp-batch --namespace galaxy

# Bind to Google service account
gcloud iam service-accounts add-iam-policy-binding \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:$PROJECT_ID.svc.id.goog[galaxy/galaxy-gcp-batch]" \
  $SA_EMAIL

# Annotate Kubernetes service account
kubectl annotate serviceaccount galaxy-gcp-batch \
  --namespace galaxy \
  iam.gke.io/gcp-service-account=$SA_EMAIL

# Update Galaxy deployment to use the service account
kubectl patch deployment galaxy \
  --namespace galaxy \
  --patch '{"spec":{"template":{"spec":{"serviceAccount":"galaxy-gcp-batch"}}}}'
```

### Update job_conf.yml for Workload Identity

```yaml
runners:
  google_batch_pulsar:
    # ... other config ...
    use_workload_identity: true
    # Remove service_account_file when using workload identity
```

## Security Best Practices

1. **Principle of Least Privilege**: Only grant the minimum required IAM roles
2. **Key Rotation**: Regularly rotate service account keys (every 90 days)
3. **Access Monitoring**: Monitor service account usage in Cloud Logging
4. **Secure Storage**: Store keys in secure locations with proper file permissions
5. **Network Security**: Use VPC firewall rules to restrict access

## Troubleshooting

### Common Issues

1. **Permission Denied**
   ```bash
   # Check service account permissions
   gcloud projects get-iam-policy $PROJECT_ID \
     --flatten="bindings[].members" \
     --format="table(bindings.role)" \
     --filter="bindings.members:$SA_EMAIL"
   ```

2. **Invalid Credentials**
   ```bash
   # Verify key file format
   python3 -c "import json; json.load(open('/path/to/key.json'))"
   
   # Test credentials
   gcloud auth activate-service-account --key-file=/path/to/key.json
   gcloud auth list
   ```

3. **API Not Enabled**
   ```bash
   # Check enabled APIs
   gcloud services list --enabled --filter="name:(batch OR storage OR compute)"
   ```

4. **Bucket Access Issues**
   ```bash
   # Test bucket access
   gsutil ls gs://your-bucket-name
   gsutil iam get gs://your-bucket-name
   ```

## Testing the Setup

After configuration, test with a simple Galaxy job:

1. Start Galaxy with the new configuration
2. Submit a test job that uses the `google_batch_pulsar` runner
3. Monitor Galaxy logs for authentication and staging activity
4. Check Google Cloud Console for Batch jobs and GCS activity

The setup is complete when you can successfully submit jobs and see them executing in Google Cloud Batch!