# --- NFS setup (pooled VM preamble) ---
echo "=== NFS Setup ==="
if ! which mount.nfs4 > /dev/null 2>&1; then
    echo "Installing nfs-common"
    apt-get update -qq && apt-get install -y nfs-common > /dev/null 2>&1 || echo "Failed to install nfs-common"
fi
mkdir -p "${nfs_mount_path}"
if mount | grep -q "${nfs_mount_path}"; then
    # Remount with actimeo=0 so files written by the Galaxy handler (task
    # payloads, marker files) appear promptly instead of waiting out the
    # default NFS attribute-cache window.
    NFS_SERVER=$$(mount | grep "${nfs_mount_path}" | cut -d: -f1)
    NFS_PATH=$$(mount | grep "${nfs_mount_path}" | cut -d: -f2 | cut -d' ' -f1)
    echo "Remounting $$NFS_SERVER:$$NFS_PATH with actimeo=0"
    umount "${nfs_mount_path}" || echo "Warning: umount failed"
    mount -t nfs4 -o rw,hard,intr,rsize=1048576,wsize=1048576,actimeo=0 "$$NFS_SERVER:$$NFS_PATH" "${nfs_mount_path}"
else
    echo "Mounting ${nfs_server}:${nfs_path} at ${nfs_mount_path}"
    mount -t nfs4 -o rw,hard,intr,rsize=1048576,wsize=1048576,actimeo=0 "${nfs_server}:${nfs_path}" "${nfs_mount_path}"
fi
if ! mount | grep -q "${nfs_mount_path}"; then
    echo "NFS mount failed for ${nfs_mount_path}"
    exit 1
fi
df -h "${nfs_mount_path}"
