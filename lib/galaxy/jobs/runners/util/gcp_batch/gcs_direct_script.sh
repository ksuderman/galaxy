#!/bin/bash
set -e
echo "=== Galaxy GCP Batch Job Execution (GCS Direct) ==="
echo "Job: ${job_id_tag}"
echo "Tool: ${tool_id}"
echo "Timestamp: $$(date)"
echo "Host: $$(hostname)"
echo ""

# Verify GCS mount — gcsfuse is auto-mounted by GCP Batch
echo "=== GCS Mount Verification ==="
if [ -d "${gcs_mount_path}" ]; then
    echo "✓ GCS mount point exists: ${gcs_mount_path}"
else
    echo "✗ GCS mount point does not exist: ${gcs_mount_path}"
    echo "GCP Batch should have auto-mounted the GCS bucket via gcsfuse"
    exit 1
fi
echo ""

# Verify job script is accessible
echo "=== Job Script Verification ==="
if [ -f "${job_file}" ]; then
    echo "✓ Galaxy job script accessible: ${job_file}"
else
    echo "✗ Galaxy job script NOT accessible: ${job_file}"
    exit 1
fi
echo ""

# Set up environment variables
export GALAXY_SLOTS=${galaxy_slots}
export GALAXY_MEMORY_MB=${galaxy_memory_mb}

echo "Environment:"
echo "  GALAXY_SLOTS=$$GALAXY_SLOTS"
echo "  GALAXY_MEMORY_MB=$$GALAXY_MEMORY_MB"
echo ""

echo "=== Starting Direct Execution ==="
cd "$$(dirname ${job_file})"
/bin/bash "${job_file}"

echo "=== Direct execution completed ==="
echo "Galaxy job execution finished"
