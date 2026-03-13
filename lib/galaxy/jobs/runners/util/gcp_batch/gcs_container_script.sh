#!/bin/bash
set -e
echo "=== Galaxy GCP Batch Job Execution (GCS) ==="
echo "Job: ${job_id_tag}"
echo "Tool: ${tool_id}"
echo "Container: ${container_image}"
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
    echo "Available files in job directory:"
    ls -la "$$(dirname ${job_file})" || echo "Could not list job directory"
    exit 1
fi
echo ""

# Check CVMFS availability (non-fatal)
echo "=== CVMFS Verification ==="
if ls /cvmfs/data.galaxyproject.org/ >/dev/null 2>&1; then
    echo "✓ CVMFS data repository is accessible at /cvmfs/data.galaxyproject.org"
else
    echo "✗ CVMFS data repository not accessible at /cvmfs/data.galaxyproject.org"
    echo "Jobs requiring reference data may fail"
fi
echo ""

if ls /cvmfs/cloud.galaxyproject.org/ >/dev/null 2>&1; then
    echo "✓ CVMFS cloud repository is accessible at /cvmfs/cloud.galaxyproject.org"
else
    echo "✗ CVMFS cloud repository not accessible at /cvmfs/cloud.galaxyproject.org"
    echo "Jobs requiring Tool Shed tools from CVMFS may fail"
fi
echo ""

# Set up environment variables for the container
export GALAXY_SLOTS=${galaxy_slots}
export GALAXY_MEMORY_MB=${galaxy_memory_mb}

echo "Container environment:"
echo "  GALAXY_SLOTS=$$GALAXY_SLOTS"
echo "  GALAXY_MEMORY_MB=$$GALAXY_MEMORY_MB"
echo ""

echo "=== Starting Container Execution ==="
docker run --rm ${docker_user_flag} \
    -v "${gcs_mount_path}:${gcs_mount_path}:rw" \
    ${docker_volume_args} \
    -w "$$(dirname ${job_file})" \
    -e GALAXY_SLOTS \
    -e GALAXY_MEMORY_MB \
    -e HOME=/tmp \
    "${container_image}" /bin/bash "${job_file}"

echo ""
echo "=== Container execution completed ==="
echo "Galaxy job execution finished"
