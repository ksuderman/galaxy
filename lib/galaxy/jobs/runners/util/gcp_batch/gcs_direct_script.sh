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

# Copy job directory from gcsfuse to local scratch.
# gcsfuse does not support mkfifo or permission preservation, which Galaxy's
# generated job scripts require. We run the job on local disk and copy
# results back to gcsfuse afterwards.
JOB_DIR="$$(dirname ${job_file})"
LOCAL_SCRATCH="/tmp/galaxy_local_scratch"
LOCAL_JOB_DIR="$${LOCAL_SCRATCH}$${JOB_DIR}"

echo "=== Staging Job to Local Scratch ==="
echo "  gcsfuse job dir: $${JOB_DIR}"
echo "  local scratch:   $${LOCAL_JOB_DIR}"
mkdir -p "$${LOCAL_JOB_DIR}"
cp -r --no-preserve=mode,ownership "$${JOB_DIR}/." "$${LOCAL_JOB_DIR}/"
LOCAL_JOB_FILE="$${LOCAL_SCRATCH}${job_file}"
echo "✓ Job files copied to local scratch"
echo ""

# Set up environment variables
export GALAXY_SLOTS=${galaxy_slots}
export GALAXY_MEMORY_MB=${galaxy_memory_mb}

echo "Environment:"
echo "  GALAXY_SLOTS=$$GALAXY_SLOTS"
echo "  GALAXY_MEMORY_MB=$$GALAXY_MEMORY_MB"
echo ""

echo "=== Starting Direct Execution ==="
cd "$${LOCAL_JOB_DIR}"
/bin/bash "${job_file}"

EXEC_EXIT=$$?

# Copy outputs back from local scratch to gcsfuse
echo "=== Staging Results Back to GCS ==="
cp -r --no-preserve=mode,ownership "$${LOCAL_JOB_DIR}/." "$${JOB_DIR}/"
echo "✓ Results copied back to GCS"

# Clean up local scratch
rm -rf "$${LOCAL_SCRATCH}"

echo "=== Direct execution completed ==="
echo "Galaxy job execution finished"
exit $${EXEC_EXIT}
