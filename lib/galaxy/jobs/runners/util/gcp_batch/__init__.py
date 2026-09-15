"""GCP Batch runner utilities and templates."""

from string import Template

from galaxy.util.resources import resource_string
from .helpers import (
    compute_machine_type,
    convert_cpu_to_milli,
    convert_duration_to_seconds,
    convert_memory_to_mib,
    DEFAULT_CPU_MILLI,
    DEFAULT_CVMFS_DOCKER_VOLUME,
    DEFAULT_MAX_RUN_DURATION,
    DEFAULT_MEMORY_MIB,
    DEFAULT_NFS_MOUNT_PATH,
    DEFAULT_NFS_PATH,
    parse_docker_volumes_param,
    parse_volume_spec,
    parse_volumes_param,
    resolve_max_run_duration,
    sanitize_label_value,
)

CONTAINER_SCRIPT_TEMPLATE = Template(resource_string(__name__, "container_script.sh"))
DIRECT_SCRIPT_TEMPLATE = Template(resource_string(__name__, "direct_script.sh"))
NFS_SETUP_TEMPLATE = Template(resource_string(__name__, "nfs_setup.sh"))
POOLED_WRAPPER_TEMPLATE = Template(resource_string(__name__, "pooled_wrapper_script.sh"))
POOLED_TASK_CONTAINER_TEMPLATE = Template(resource_string(__name__, "pooled_task_container.sh"))


def render_pooled_wrapper_script(
    nfs_server: str,
    nfs_path: str,
    nfs_mount_path: str,
    vm_dir: str,
    pool_ttl_seconds: int,
    vm_lifetime_seconds: int,
) -> str:
    """Render the pooled VM wrapper, composing in the NFS setup preamble."""
    nfs_setup = NFS_SETUP_TEMPLATE.substitute(
        nfs_server=nfs_server,
        nfs_path=nfs_path,
        nfs_mount_path=nfs_mount_path,
    )
    return POOLED_WRAPPER_TEMPLATE.substitute(
        nfs_setup=nfs_setup,
        vm_dir=vm_dir,
        pool_ttl_seconds=pool_ttl_seconds,
        vm_lifetime_seconds=vm_lifetime_seconds,
    )


__all__ = (
    "CONTAINER_SCRIPT_TEMPLATE",
    "NFS_SETUP_TEMPLATE",
    "POOLED_TASK_CONTAINER_TEMPLATE",
    "POOLED_WRAPPER_TEMPLATE",
    "render_pooled_wrapper_script",
    "DEFAULT_CPU_MILLI",
    "DEFAULT_CVMFS_DOCKER_VOLUME",
    "DEFAULT_MAX_RUN_DURATION",
    "DEFAULT_MEMORY_MIB",
    "DEFAULT_NFS_MOUNT_PATH",
    "DEFAULT_NFS_PATH",
    "DIRECT_SCRIPT_TEMPLATE",
    "compute_machine_type",
    "convert_cpu_to_milli",
    "convert_memory_to_mib",
    "convert_duration_to_seconds",
    "parse_docker_volumes_param",
    "resolve_max_run_duration",
    "parse_volume_spec",
    "parse_volumes_param",
    "sanitize_label_value",
)
