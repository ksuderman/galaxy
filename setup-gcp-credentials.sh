#!/bin/bash
# Google Cloud Credentials Setup Script for Galaxy GCP Batch Pulsar Runner
# Usage: ./setup-gcp-credentials.sh PROJECT_ID [REGION]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}$1${NC}"
}

# Check if project ID is provided
if [ $# -lt 1 ]; then
    print_error "Usage: $0 PROJECT_ID [REGION]"
    print_error "Example: $0 my-galaxy-project us-central1"
    exit 1
fi

PROJECT_ID="$1"
REGION="${2:-us-central1}"
ZONE="${REGION}-a"

print_header "=== Galaxy GCP Batch Pulsar Credentials Setup ==="
print_status "Project ID: $PROJECT_ID"
print_status "Region: $REGION"
print_status "Zone: $ZONE"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    print_error "gcloud CLI is not installed. Please install it first:"
    print_error "https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if user is authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q "."; then
    print_error "Please authenticate with gcloud first:"
    print_error "gcloud auth login"
    exit 1
fi

# Set project
print_status "Setting project configuration..."
gcloud config set project "$PROJECT_ID"

# Enable APIs
print_header "\n=== Enabling Required APIs ==="
apis=(
    "batch.googleapis.com"
    "storage-api.googleapis.com" 
    "compute.googleapis.com"
    "iam.googleapis.com"
)

for api in "${apis[@]}"; do
    print_status "Enabling $api..."
    gcloud services enable "$api"
done

# Create service account
print_header "\n=== Creating Service Account ==="
SA_NAME="galaxy-gcp-batch"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "$SA_EMAIL" &>/dev/null; then
    print_warning "Service account $SA_EMAIL already exists"
else
    print_status "Creating service account: $SA_NAME"
    gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Galaxy GCP Batch Runner" \
        --description="Service account for Galaxy GCP Batch job execution"
fi

# Assign IAM roles
print_header "\n=== Assigning IAM Roles ==="
roles=(
    "roles/batch.jobsEditor"
    "roles/storage.objectAdmin"
    "roles/compute.instanceAdmin.v1"
    "roles/iam.serviceAccountUser"
    "roles/logging.logWriter"
)

for role in "${roles[@]}"; do
    print_status "Assigning role: $role"
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --quiet
done

# Create service account key
print_header "\n=== Creating Service Account Key ==="
KEY_FILE="galaxy-gcp-service-account.json"
CREDENTIALS_DIR="/etc/galaxy/credentials"

if [ -f "$KEY_FILE" ]; then
    print_warning "Key file $KEY_FILE already exists. Creating backup..."
    mv "$KEY_FILE" "${KEY_FILE}.backup.$(date +%Y%m%d-%H%M%S)"
fi

print_status "Creating service account key..."
gcloud iam service-accounts keys create "$KEY_FILE" \
    --iam-account="$SA_EMAIL"

# Secure the key file
print_status "Securing key file..."
chmod 600 "$KEY_FILE"

# Create credentials directory and move key
if [ ! -d "$CREDENTIALS_DIR" ]; then
    print_status "Creating credentials directory: $CREDENTIALS_DIR"
    sudo mkdir -p "$CREDENTIALS_DIR"
fi

print_status "Moving key to secure location..."
sudo mv "$KEY_FILE" "$CREDENTIALS_DIR/"
sudo chown root:root "${CREDENTIALS_DIR}/${KEY_FILE}"
sudo chmod 400 "${CREDENTIALS_DIR}/${KEY_FILE}"

# Create GCS bucket
print_header "\n=== Creating GCS Bucket ==="
BUCKET_NAME="galaxy-batch-staging-${PROJECT_ID}"

if gsutil ls "gs://$BUCKET_NAME" &>/dev/null; then
    print_warning "Bucket gs://$BUCKET_NAME already exists"
else
    print_status "Creating bucket: gs://$BUCKET_NAME"
    gsutil mb -p "$PROJECT_ID" -c STANDARD -l "$REGION" "gs://$BUCKET_NAME"
    
    print_status "Setting bucket permissions..."
    gsutil iam ch "serviceAccount:${SA_EMAIL}:objectAdmin" "gs://$BUCKET_NAME"
fi

# Generate configuration templates
print_header "\n=== Generating Configuration Templates ==="

# job_conf.yml snippet
cat > "job_conf_gcp_batch_pulsar.yml" << EOF
# Add this to your job_conf.yml

runners:
  google_batch_pulsar:
    load: galaxy.jobs.runners.gcp_batch_pulsar:GCPBatchPulsarJobRunner
    
    # Google Cloud Configuration
    project_id: $PROJECT_ID
    region: $REGION
    zone: $ZONE
    
    # Service Account Authentication
    service_account_file: ${CREDENTIALS_DIR}/${KEY_FILE}
    
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
      bucket: $BUCKET_NAME
      mount_path: /mnt/galaxy-data
      
    # Embedded Pulsar Configuration
    pulsar_embedded_config:
      staging_directory: /tmp/pulsar-staging
      galaxy_url: http://localhost:8080
      private_token: $(openssl rand -hex 32)
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
EOF

# galaxy.yml snippet  
cat > "galaxy_gcp_config.yml" << EOF
# Add this to your galaxy.yml

google_batch:
  enabled: true
  project_id: $PROJECT_ID
  default_region: $REGION
  service_account_file: ${CREDENTIALS_DIR}/${KEY_FILE}

object_store_config_file: config/object_store_conf.yml
file_sources_config_file: config/file_sources_conf.yml
EOF

# object_store_conf.yml
cat > "object_store_conf.yml" << EOF
type: cloud
provider: google
auth:
  credentials_file: ${CREDENTIALS_DIR}/${KEY_FILE}

bucket:
  name: $BUCKET_NAME
  use_reduced_redundancy: false

cache:
  path: database/object_store_cache
  size: 10000

extra_dirs:
- type: job_work
  path: database/job_working_directory_gcp
- type: temp
  path: database/tmp_gcp
EOF

# file_sources_conf.yml snippet
cat > "file_sources_gcp.yml" << EOF
# Add this to your file_sources_conf.yml

- type: googlecloudstorage
  id: galaxy_gcs_batch
  label: Galaxy GCS Batch Storage
  bucket: $BUCKET_NAME
  service_account_file: ${CREDENTIALS_DIR}/${KEY_FILE}
  writable: true
EOF

# Environment variables
cat > "gcp_environment.sh" << EOF
#!/bin/bash
# Source this file to set GCP environment variables

export GOOGLE_APPLICATION_CREDENTIALS="${CREDENTIALS_DIR}/${KEY_FILE}"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
EOF

chmod +x "gcp_environment.sh"

# Create test script
cat > "test_gcp_auth.py" << 'EOF'
#!/usr/bin/env python3
"""Test script for GCP authentication and permissions"""

import os
import sys

def test_authentication():
    """Test GCP authentication and basic API access"""
    print("Testing GCP Authentication...")
    
    try:
        from google.auth import default
        from google.cloud import batch_v1
        from google.cloud import storage
    except ImportError as e:
        print(f"❌ Missing required library: {e}")
        print("Install with: pip install google-cloud-batch google-cloud-storage")
        return False
    
    try:
        # Test authentication
        credentials, project = default()
        print(f"✓ Authentication successful for project: {project}")
        
        # Test Batch API
        batch_client = batch_v1.BatchServiceClient(credentials=credentials)
        print("✓ Batch API client created successfully")
        
        # Test Storage API  
        storage_client = storage.Client(credentials=credentials, project=project)
        print("✓ Storage API client created successfully")
        
        # Test bucket access
        try:
            buckets = list(storage_client.list_buckets())
            print(f"✓ Can access {len(buckets)} storage buckets")
        except Exception as e:
            print(f"⚠️  Limited bucket access: {e}")
            
        print("\n🎉 Authentication test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Authentication test failed: {e}")
        return False

if __name__ == "__main__":
    success = test_authentication()
    sys.exit(0 if success else 1)
EOF

chmod +x "test_gcp_auth.py"

# Summary
print_header "\n=== Setup Complete! ==="
print_status "Service Account: $SA_EMAIL"
print_status "Credentials File: ${CREDENTIALS_DIR}/${KEY_FILE}"
print_status "GCS Bucket: gs://$BUCKET_NAME"

print_header "\n=== Generated Files ==="
print_status "job_conf_gcp_batch_pulsar.yml - Job runner configuration"
print_status "galaxy_gcp_config.yml - Galaxy configuration"  
print_status "object_store_conf.yml - Object store configuration"
print_status "file_sources_gcp.yml - File sources configuration"
print_status "gcp_environment.sh - Environment variables"
print_status "test_gcp_auth.py - Authentication test script"

print_header "\n=== Next Steps ==="
echo "1. Merge the configuration snippets into your Galaxy config files"
echo "2. Install required Python packages:"
echo "   pip install google-cloud-batch google-cloud-storage google-auth"
echo "3. Source environment variables:"
echo "   source ./gcp_environment.sh"
echo "4. Test authentication:"
echo "   python3 test_gcp_auth.py"
echo "5. Restart Galaxy with the new configuration"

print_warning "\nIMPORTANT: Keep your service account key secure and never commit it to version control!"