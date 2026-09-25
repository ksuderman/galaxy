#!/bin/bash
# GALAXY_JOB_ID=${galaxy_job_id}
# Per-job payload dropped into a pooled VM's handoff directory as task.sh.
# Executed by pooled_wrapper_script.sh after it claims the file.
echo "=== Galaxy pooled job ${job_id_tag} ==="
echo "Tool: ${tool_id}"
echo "Container: ${container_image}"
echo "Timestamp: $$(date)"
echo ""

export GALAXY_SLOTS=${galaxy_slots}
export GALAXY_MEMORY_MB=${galaxy_memory_mb}

if [ ! -f "${job_file}" ]; then
    echo "Galaxy job script not accessible: ${job_file}"
    exit 1
fi

# The container is named so the wrapper's cancel watcher can kill it, and the
# whole run is bounded by the per-job walltime (the Batch max_run_duration
# covers the VM lifetime, not this job, so the walltime is enforced here).
timeout ${job_walltime_seconds} docker run --rm --name "galaxy-job-${galaxy_job_id}" ${docker_user_flag} \
    -v "${nfs_mount_path}:${nfs_mount_path}:rw" \
    ${docker_volume_args} \
    -w "$$(dirname ${job_file})" \
    -e GALAXY_SLOTS="$$GALAXY_SLOTS" \
    -e GALAXY_MEMORY_MB="$$GALAXY_MEMORY_MB" \
    -e HOME=/tmp \
    "${container_image}" /bin/bash "${job_file}"
EXIT_CODE=$$?
echo ""
echo "=== Pooled job ${job_id_tag} completed with exit code $$EXIT_CODE ==="
exit $$EXIT_CODE
