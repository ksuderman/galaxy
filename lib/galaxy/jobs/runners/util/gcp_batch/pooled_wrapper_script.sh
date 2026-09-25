#!/bin/bash
# Galaxy GCP Batch pooled VM wrapper.
#
# Runs as the single Batch Runnable of a pooled VM. After the NFS preamble it
# enters a claim loop: it watches the VM's handoff directory for a task.sh
# payload, claims it with an atomic rename, runs it, writes a done marker with
# the exit code, and goes back to idling. It exits (and the Batch job SUCCEEDs,
# so GCP tears the VM down) when the idle TTL passes without new work, when the
# VM lifetime deadline approaches, when the runner drops a shutdown marker, or
# after a cancelled job. Teardown is therefore guaranteed by the script itself
# even if the Galaxy handler crashes.
#
# Deliberately no set -e around the loop: a failing job payload must not kill
# the wrapper.
echo "=== Galaxy GCP Batch Pooled VM ==="
echo "VM dir: ${vm_dir}"
echo "Idle TTL: ${pool_ttl_seconds}s  Lifetime: ${vm_lifetime_seconds}s"
echo "Timestamp: $$(date)"
echo "Host: $$(hostname)"
echo ""

${nfs_setup}

VM_DIR="${vm_dir}"
mkdir -p "$$VM_DIR"

STATE_FILE=/tmp/galaxy-pool-state
CANCEL_FLAG=/tmp/galaxy-pool-cancelled
echo "idle" > "$$STATE_FILE"
rm -f "$$CANCEL_FLAG"

# Heartbeat: copy the local state file to the shared heartbeat file every 15s.
# The runner treats a heartbeat older than 90s as a dead VM.
(
    while true; do
        cp "$$STATE_FILE" "$$VM_DIR/heartbeat" 2>/dev/null || true
        sleep 15
    done
) &
HEARTBEAT_PID=$$!

touch "$$VM_DIR/ready"

START_TS=$$(date +%s)
LIFETIME_DEADLINE=$$((START_TS + ${vm_lifetime_seconds} - 120))
IDLE_DEADLINE=$$((START_TS + ${pool_ttl_seconds}))

while true; do
    NOW=$$(date +%s)
    if [ -f "$$VM_DIR/shutdown" ]; then
        echo "Shutdown marker found; exiting"
        break
    fi
    if [ "$$NOW" -ge "$$IDLE_DEADLINE" ]; then
        echo "Idle TTL reached; exiting"
        break
    fi
    if [ "$$NOW" -ge "$$LIFETIME_DEADLINE" ]; then
        echo "VM lifetime deadline reached; exiting"
        break
    fi
    # The claim: rename task.sh -> task.claimed.sh. Rename is atomic on the NFS
    # server; if the runner reclaimed the payload first, this mv fails and we
    # simply keep looping.
    if [ -f "$$VM_DIR/task.sh" ] && mv "$$VM_DIR/task.sh" "$$VM_DIR/task.claimed.sh" 2>/dev/null; then
        GALAXY_JOB_ID=$$(sed -n 's/^# GALAXY_JOB_ID=//p' "$$VM_DIR/task.claimed.sh" | head -1)
        echo "Claimed job $$GALAXY_JOB_ID"
        echo "busy $$GALAXY_JOB_ID" > "$$STATE_FILE"

        # Watch for a cancel marker while the job runs; the payload's container
        # is named galaxy-job-<id> so it can be killed here.
        (
            while [ ! -f "$$VM_DIR/cancel" ]; do
                sleep 2
            done
            echo "Cancel marker found; killing container galaxy-job-$$GALAXY_JOB_ID"
            docker rm -f "galaxy-job-$$GALAXY_JOB_ID" > /dev/null 2>&1 || true
            touch "$$CANCEL_FLAG"
        ) &
        CANCEL_WATCHER_PID=$$!

        /bin/bash "$$VM_DIR/task.claimed.sh"
        EXIT_CODE=$$?
        kill "$$CANCEL_WATCHER_PID" 2>/dev/null || true

        echo "Job $$GALAXY_JOB_ID finished with exit code $$EXIT_CODE"
        NONCE=$$RANDOM
        echo "$$EXIT_CODE" > "$$VM_DIR/.done.tmp.$$NONCE"
        mv "$$VM_DIR/.done.tmp.$$NONCE" "$$VM_DIR/done-$$GALAXY_JOB_ID"
        rm -f "$$VM_DIR/task.claimed.sh"
        echo "idle" > "$$STATE_FILE"

        if [ -f "$$CANCEL_FLAG" ]; then
            echo "Job was cancelled; exiting"
            break
        fi
        IDLE_DEADLINE=$$(($$(date +%s) + ${pool_ttl_seconds}))
    else
        sleep 2
    fi
done

touch "$$VM_DIR/exiting"
kill "$$HEARTBEAT_PID" 2>/dev/null || true
echo "Pooled VM wrapper exiting"
exit 0
