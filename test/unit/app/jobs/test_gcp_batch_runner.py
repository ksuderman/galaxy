"""Unit tests for Google Cloud Batch job runner utility methods."""

from types import SimpleNamespace
from unittest import mock

import pytest
from google.api_core import exceptions as gcp_exceptions
from google.cloud import batch_v1

from galaxy import model
from galaxy.jobs.runners.gcp_batch import GoogleCloudBatchJobRunner
from galaxy.jobs.runners.util.gcp_batch import (
    compute_machine_type,
    convert_cpu_to_milli,
    convert_duration_to_seconds,
    convert_memory_to_mib,
    DEFAULT_CVMFS_DOCKER_VOLUME,
    DEFAULT_MAX_RUN_DURATION,
    DEFAULT_MEMORY_MIB,
    DEFAULT_NFS_MOUNT_PATH,
    parse_docker_volumes_param,
    parse_volume_spec,
    parse_volumes_param,
    resolve_max_run_duration,
    sanitize_label_value,
)


class TestSanitizeLabelValue:
    """Tests for sanitize_label_value helper function."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("HelloWorld", "helloworld"),  # lowercase conversion
            ("tool@1.0", "tool-1-0"),  # invalid chars replaced
            ("a--b---c", "a-b-c"),  # consecutive dashes collapsed
            ("--value--", "value"),  # leading/trailing dashes stripped
            ("", "unknown"),  # empty string
            (None, "unknown"),  # None value
            ("@#$%", "unknown"),  # all invalid chars
            ("valid-label_123", "valid-label-123"),  # underscores replaced with dashes
            ("a" * 100, "a" * 63),  # truncation at max_length
            ("a" * 62 + "-x", "a" * 62),  # a truncated identified does not end with a dash
        ],
    )
    def test_sanitize_label_value(self, input_value, expected):
        result = sanitize_label_value(input_value)
        assert result == expected


class TestConvertCpuToMilli:
    """Tests for convert_cpu_to_milli helper function."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("2", 2000),  # integer string
            ("1.5", 1500),  # decimal string
            ("500m", 500),  # milli format
            ("0.25", 250),  # fractional cpu
            ("", 1000),  # empty string -> default
            (None, 1000),  # None -> default
            ("abcm", 1000),  # invalid milli format -> default
            ("invalid", 1000),  # invalid format -> default
            ("1", 1000),  # single cpu
            ("4", 4000),  # four cpus
            ("100m", 100),  # small milli value
            ("2500m", 2500),  # larger milli value
        ],
    )
    def test_convert_cpu_to_milli(self, input_value, expected):
        result = convert_cpu_to_milli(input_value)
        assert result == expected


class TestConvertMemoryToMib:
    """Tests for convert_memory_to_mib helper function."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("2048", 2048),  # plain number
            ("512Mi", 512),  # MiB suffix
            ("512MiB", 512),  # MiB suffix (full)
            ("2Gi", 2048),  # GiB suffix
            ("1gib", 1024),  # GiB lowercase
            ("1024", 1024),  # plain number
            ("", 2048),  # empty -> default
            (None, 2048),  # None -> default
            ("invalid-format", 2048),  # invalid -> default
            ("1.5Gi", 1536),  # decimal GiB
            ("256Mi", 256),  # small MiB value
            ("4Gi", 4096),  # larger GiB value
        ],
    )
    def test_convert_memory_to_mib(self, input_value, expected):
        result = convert_memory_to_mib(input_value)
        assert result == expected


class TestParseVolumeSpec:
    """Tests for parse_volume_spec helper function."""

    def test_basic_nfs_volume(self):
        result = parse_volume_spec("10.0.0.1:/galaxy:/mnt/nfs")
        assert result == {
            "server": "10.0.0.1",
            "remote_path": "/galaxy",
            "mount_path": "/mnt/nfs",
            "read_only": False,
        }

    def test_read_only_volume(self):
        result = parse_volume_spec("nfs-server:/exports:/data:ro")
        assert result == {
            "server": "nfs-server",
            "remote_path": "/exports",
            "mount_path": "/data",
            "read_only": True,
        }

    def test_read_only_with_r(self):
        result = parse_volume_spec("server:/path:/mount:r")
        assert result["read_only"] is True

    def test_read_only_with_readonly(self):
        result = parse_volume_spec("server:/path:/mount:readonly")
        assert result["read_only"] is True

    def test_invalid_too_few_parts(self):
        result = parse_volume_spec("server:/path")
        assert result is None

    def test_empty_string(self):
        result = parse_volume_spec("")
        assert result is None

    def test_none_input(self):
        result = parse_volume_spec(None)
        assert result is None

    def test_whitespace_handling(self):
        result = parse_volume_spec(" server : /path : /mount ")
        assert result == {
            "server": "server",
            "remote_path": "/path",
            "mount_path": "/mount",
            "read_only": False,
        }


class TestParseVolumesParam:
    """Tests for parse_volumes_param helper function."""

    def test_single_volume(self):
        result = parse_volumes_param("10.0.0.1:/galaxy:/mnt/nfs")
        assert len(result) == 1
        assert result[0]["server"] == "10.0.0.1"

    def test_multiple_volumes(self):
        result = parse_volumes_param("10.0.0.1:/galaxy:/mnt/nfs,cvmfs:/cvmfs:/cvmfs:ro")
        assert len(result) == 2
        assert result[0]["server"] == "10.0.0.1"
        assert result[0]["read_only"] is False
        assert result[1]["server"] == "cvmfs"
        assert result[1]["read_only"] is True

    def test_empty_string(self):
        result = parse_volumes_param("")
        assert result == []

    def test_none_input(self):
        result = parse_volumes_param(None)
        assert result == []

    def test_whitespace_between_volumes(self):
        result = parse_volumes_param("server1:/p1:/m1 , server2:/p2:/m2")
        assert len(result) == 2

    def test_invalid_volumes_skipped(self):
        result = parse_volumes_param("valid:/path:/mount,invalid,another:/p:/m")
        assert len(result) == 2


class TestParseDockerVolumesParam:
    """Tests for parse_docker_volumes_param helper function."""

    def test_single_volume(self):
        result = parse_docker_volumes_param("/host/path:/container/path")
        assert result == '-v "/host/path:/container/path"'

    def test_multiple_volumes(self):
        result = parse_docker_volumes_param("/path1:/mount1,/path2:/mount2:ro")
        assert result == '-v "/path1:/mount1" -v "/path2:/mount2:ro"'

    def test_empty_string(self):
        result = parse_docker_volumes_param("")
        assert result == ""

    def test_none_input(self):
        result = parse_docker_volumes_param(None)
        assert result == ""

    def test_cvmfs_example(self):
        result = parse_docker_volumes_param("/cvmfs/data.galaxyproject.org:/cvmfs/data.galaxyproject.org:ro")
        assert result == '-v "/cvmfs/data.galaxyproject.org:/cvmfs/data.galaxyproject.org:ro"'


class TestConvertDurationToSeconds:
    """Tests for convert_duration_to_seconds helper function."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("3600s", "3600s"),  # seconds suffix
            ("86400s", "86400s"),  # larger seconds value
            ("0s", "0s"),  # zero seconds
            ("30m", "1800s"),  # minutes to seconds
            ("90m", "5400s"),  # larger minutes
            ("2h", "7200s"),  # hours to seconds
            ("24h", "86400s"),  # 24 hours
            ("1d", "86400s"),  # days to seconds
            ("7d", "604800s"),  # 7 days
            ("3600", "3600s"),  # plain integer string (seconds assumed)
            ("86400", "86400s"),  # plain integer string
            (3600, "3600s"),  # numeric int
            (7200, "7200s"),  # numeric int
            (7200.0, "7200s"),  # numeric float
            ("1.5h", "5400s"),  # fractional hours
            ("2.5d", "216000s"),  # fractional days
            ("0.5h", "1800s"),  # half hour
            ("", DEFAULT_MAX_RUN_DURATION),  # empty string -> default
            (None, DEFAULT_MAX_RUN_DURATION),  # None -> default
            ("invalid", DEFAULT_MAX_RUN_DURATION),  # garbage -> default
            ("abcs", DEFAULT_MAX_RUN_DURATION),  # invalid with s suffix -> default
            ("xxh", DEFAULT_MAX_RUN_DURATION),  # invalid with h suffix -> default
            ("zzm", DEFAULT_MAX_RUN_DURATION),  # invalid with m suffix -> default
            ("qqd", DEFAULT_MAX_RUN_DURATION),  # invalid with d suffix -> default
        ],
    )
    def test_convert_duration_to_seconds(self, input_value, expected):
        result = convert_duration_to_seconds(input_value)
        assert result == expected


class TestResolveMaxRunDuration:
    """Tests for resolve_max_run_duration priority resolution."""

    def test_resource_param_walltime_highest_priority(self):
        """User-specified walltime wins over everything."""
        result = resolve_max_run_duration(
            destination_params={"max_run_duration": "2h"},
            runner_params={"max_run_duration": "1h"},
            resource_params={"walltime": "3600"},
        )
        assert result == "3600s"

    def test_destination_max_run_duration_over_dest_walltime(self):
        """Destination 'max_run_duration' beats destination 'walltime'."""
        result = resolve_max_run_duration(
            destination_params={"max_run_duration": "2h", "walltime": "1h"},
            runner_params={"max_run_duration": "86400s"},
            resource_params={},
        )
        assert result == "7200s"

    def test_destination_walltime_over_runner_default(self):
        """Destination 'walltime' beats the runner-level default."""
        result = resolve_max_run_duration(
            destination_params={"walltime": "4h"},
            runner_params={"max_run_duration": "86400s"},
            resource_params={},
        )
        assert result == "14400s"

    def test_runner_default_fallback(self):
        """Falls back to runner-level max_run_duration when nothing else set."""
        result = resolve_max_run_duration(
            destination_params={},
            runner_params={"max_run_duration": "3600s"},
            resource_params={},
        )
        assert result == "3600s"

    def test_global_default_when_nothing_set(self):
        """Falls back to DEFAULT_MAX_RUN_DURATION when params dict has no key."""
        result = resolve_max_run_duration(
            destination_params={},
            runner_params={},
            resource_params={},
        )
        assert result == DEFAULT_MAX_RUN_DURATION

    def test_resource_walltime_over_destination_max_run_duration(self):
        """User walltime overrides destination max_run_duration."""
        result = resolve_max_run_duration(
            destination_params={"max_run_duration": "1h"},
            runner_params={"max_run_duration": "86400s"},
            resource_params={"walltime": "2d"},
        )
        assert result == "172800s"

    def test_destination_max_run_duration_normalizes_format(self):
        """Duration values are normalized through convert_duration_to_seconds."""
        result = resolve_max_run_duration(
            destination_params={"max_run_duration": "48h"},
            runner_params={},
            resource_params={},
        )
        assert result == "172800s"

    def test_empty_walltime_ignored(self):
        """Empty walltime in resource params is skipped."""
        result = resolve_max_run_duration(
            destination_params={},
            runner_params={"max_run_duration": "7200s"},
            resource_params={"walltime": ""},
        )
        assert result == "7200s"


# ---------------------------------------------------------------------------
# Tier 1: pure / near-pure logic
# ---------------------------------------------------------------------------


class TestComputeMachineType:
    """Tests for compute_machine_type variant selection and size snapping."""

    @pytest.mark.parametrize(
        "cpu_milli,memory_mib,expected",
        [
            (1000, 2048, "n2-highcpu-4"),  # 2.0 GB/vCPU ratio -> highcpu, snaps up to size 4
            (500, 512, "n2-highcpu-2"),  # tiny job -> smallest valid size
            (4000, 16384, "n2-standard-4"),  # 4.0 GB/vCPU -> standard
            (2000, 12288, "n2-standard-4"),  # ratio exactly 6.0 -> standard (boundary)
            (2000, 13312, "n2-highmem-2"),  # ratio 6.5 (just over 6) -> highmem
            (2000, 32768, "n2-highmem-4"),  # memory-heavy -> highmem
            (200000, 2048, "n2-highcpu-128"),  # exceeds largest size -> capped at 128
        ],
    )
    def test_compute_machine_type(self, cpu_milli, memory_mib, expected):
        assert compute_machine_type(cpu_milli, memory_mib) == expected

    def test_custom_machine_family(self):
        assert compute_machine_type(4000, 16384, machine_type_family="n1") == "n1-standard-4"


class TestConvertMemoryToMibUnitBranches:
    """Coverage for the memory unit branches not exercised by TestConvertMemoryToMib."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("1024kib", 1),  # KiB -> MiB (value / 1024)
            ("2048ki", 2),
            ("1024mb", 1000),  # decimal MB -> MiB
            ("1000m", 976),
            ("1048576kb", 1000),  # decimal KB -> MiB
            ("5xyz", 5),  # unknown unit -> treated as MiB
        ],
    )
    def test_unit_conversions(self, input_value, expected):
        assert convert_memory_to_mib(input_value) == expected

    @pytest.mark.xfail(
        strict=True,
        reason="GB/G branch is off by ~1000x (divides bytes by 1024^2 without scaling GB to bytes); "
        "e.g. '1gb' returns 0 instead of ~953 MiB. Remove this xfail when helpers.py is fixed.",
    )
    @pytest.mark.parametrize("input_value,expected", [("1gb", 953), ("4gb", 3814)])
    def test_gb_branch_should_convert_decimal_gb(self, input_value, expected):
        assert convert_memory_to_mib(input_value) == expected


def _bare_runner():
    """A runner instance with __init__ skipped (no GCP client).

    The resource/script methods under test read only their arguments, not the
    live Batch client, so a bare instance is sufficient.
    """
    return object.__new__(GoogleCloudBatchJobRunner)


def _destination(params=None):
    return SimpleNamespace(params=params or {})


class TestGetCpuMilli:
    """Tests for _get_cpu_milli priority resolution."""

    def test_requests_cpu_has_highest_priority(self):
        runner = _bare_runner()
        dest = _destination({"requests_cpu": "2", "limits_cpu": "8", "cores": "16"})
        assert runner._get_cpu_milli(dest, {"vcpu": 1.0}, {"processors": 32}) == 2000

    def test_limits_cpu_used_when_no_requests(self):
        runner = _bare_runner()
        dest = _destination({"limits_cpu": "500m"})
        assert runner._get_cpu_milli(dest, {}, {}) == 500

    def test_resource_processors_over_destination_cores(self):
        runner = _bare_runner()
        dest = _destination({"cores": "16"})
        assert runner._get_cpu_milli(dest, {}, {"processors": 4}) == 4000

    def test_destination_cores(self):
        runner = _bare_runner()
        dest = _destination({"cores": "3"})
        assert runner._get_cpu_milli(dest, {}, {}) == 3000

    def test_default_vcpu(self):
        runner = _bare_runner()
        assert runner._get_cpu_milli(_destination(), {"vcpu": 2.5}, {}) == 2500

    def test_default_vcpu_falls_back_to_one(self):
        runner = _bare_runner()
        assert runner._get_cpu_milli(_destination(), {}, {}) == 1000


class TestGetMemoryMib:
    """Tests for _get_memory_mib priority resolution."""

    def test_requests_memory_has_highest_priority(self):
        runner = _bare_runner()
        dest = _destination({"requests_memory": "2Gi", "limits_memory": "8Gi", "mem": "64"})
        assert runner._get_memory_mib(dest, {"memory_mib": 512}, {"mem": 128}) == 2048

    def test_limits_memory_used_when_no_requests(self):
        runner = _bare_runner()
        dest = _destination({"limits_memory": "512Mi"})
        assert runner._get_memory_mib(dest, {}, {}) == 512

    def test_resource_mem_gb_over_destination_mem(self):
        runner = _bare_runner()
        dest = _destination({"mem": "64"})
        assert runner._get_memory_mib(dest, {}, {"mem": 4}) == 4096  # 4 GB -> MiB

    def test_destination_mem_gb(self):
        runner = _bare_runner()
        dest = _destination({"mem": "2"})
        assert runner._get_memory_mib(dest, {}, {}) == 2048  # 2 GB -> MiB

    def test_default_memory_mib(self):
        runner = _bare_runner()
        assert runner._get_memory_mib(_destination(), {"memory_mib": 4096}, {}) == 4096

    def test_default_memory_falls_back_to_constant(self):
        runner = _bare_runner()
        assert runner._get_memory_mib(_destination(), {}, {}) == DEFAULT_MEMORY_MIB


# ---------------------------------------------------------------------------
# Tier 2: methods needing lightweight fakes / mocks
# ---------------------------------------------------------------------------


def _job_wrapper(tool_id="cat1", id_tag="42"):
    return SimpleNamespace(get_id_tag=lambda: id_tag, tool=SimpleNamespace(id=tool_id))


class TestCreateContainerExecutionScript:
    """Tests for the container execution script builder."""

    def test_docker_user_flag_user_and_group(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/data/jobs/galaxy_42.sh")
        params = {"galaxy_user_id": "1000", "galaxy_group_id": "1000"}
        script = runner._create_container_execution_script(_job_wrapper(), ajs, params, "busybox:latest", 4000, 8192)
        assert "--user 1000:1000" in script
        assert "export GALAXY_SLOTS=4" in script
        assert "busybox:latest" in script

    def test_docker_user_flag_user_only(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_container_execution_script(
            _job_wrapper(), ajs, {"galaxy_user_id": "1000"}, "img", 1000, 1024
        )
        assert "--user 1000" in script
        assert "--user 1000:" not in script

    def test_no_user_flag_when_unset(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_container_execution_script(_job_wrapper(), ajs, {}, "img", 1000, 1024)
        assert "--user" not in script

    def test_cvmfs_default_volume_when_no_extra_volumes(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_container_execution_script(_job_wrapper(), ajs, {}, "img", 1000, 1024)
        assert DEFAULT_CVMFS_DOCKER_VOLUME in script

    def test_nfs_fallback_when_no_volumes(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_container_execution_script(_job_wrapper(), ajs, {}, "img", 1000, 1024)
        assert "127.0.0.1" in script  # default nfs_server fallback
        assert DEFAULT_NFS_MOUNT_PATH in script

    def test_galaxy_slots_minimum_one(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_container_execution_script(_job_wrapper(), ajs, {}, "img", 500, 1024)
        assert "export GALAXY_SLOTS=1" in script  # max(1, 500 // 1000) == 1

    def test_parsed_volume_used(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        params = {"gcp_batch_volumes": "10.0.0.1:/galaxy:/mnt/nfs"}
        script = runner._create_container_execution_script(_job_wrapper(), ajs, params, "img", 1000, 1024)
        assert "10.0.0.1" in script
        assert "/mnt/nfs" in script


class TestCreateDirectExecutionScript:
    """Tests for the direct (no container) execution script builder."""

    def test_galaxy_slots_and_memory(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_direct_execution_script(_job_wrapper(), ajs, {}, 8000, 4096)
        assert "export GALAXY_SLOTS=8" in script
        assert "export GALAXY_MEMORY_MB=4096" in script

    def test_nfs_mount_fallback(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        script = runner._create_direct_execution_script(_job_wrapper(), ajs, {}, 1000, 1024)
        assert DEFAULT_NFS_MOUNT_PATH in script

    def test_parsed_volume_mount_used(self):
        runner = _bare_runner()
        ajs = SimpleNamespace(job_file="/j.sh")
        params = {"gcp_batch_volumes": "srv:/exports:/custom/mount"}
        script = runner._create_direct_execution_script(_job_wrapper(), ajs, params, 1000, 1024)
        assert "/custom/mount" in script


def _watched_runner():
    runner = _bare_runner()
    runner.runner_params = {"project_id": "proj", "region": "us-central1"}
    runner.batch_client = mock.Mock()
    runner.mark_as_finished = mock.Mock()
    runner.mark_as_failed = mock.Mock()
    return runner


def _job_state():
    return SimpleNamespace(job_id="batch-job-1", running=None, job_wrapper=mock.Mock())


def _batch_job_with_state(state):
    job = mock.Mock()
    job.status.state = state
    return job


class TestCheckWatchedItem:
    """Tests for the Batch-state -> Galaxy-state mapping in check_watched_item."""

    def test_succeeded_marks_finished_and_stops(self):
        runner = _watched_runner()
        runner.batch_client.get_job.return_value = _batch_job_with_state(batch_v1.JobStatus.State.SUCCEEDED)
        js = _job_state()
        assert runner.check_watched_item(js) is None
        assert js.running is False
        js.job_wrapper.change_state.assert_called_once_with(model.Job.states.OK)
        runner.mark_as_finished.assert_called_once_with(js)

    def test_failed_marks_failed_and_stops(self):
        runner = _watched_runner()
        runner.batch_client.get_job.return_value = _batch_job_with_state(batch_v1.JobStatus.State.FAILED)
        js = _job_state()
        assert runner.check_watched_item(js) is None
        assert js.running is False
        js.job_wrapper.change_state.assert_called_once_with(model.Job.states.ERROR)
        runner.mark_as_failed.assert_called_once_with(js)

    def test_running_keeps_monitoring(self):
        runner = _watched_runner()
        runner.batch_client.get_job.return_value = _batch_job_with_state(batch_v1.JobStatus.State.RUNNING)
        js = _job_state()
        assert runner.check_watched_item(js) is js
        assert js.running is True
        js.job_wrapper.change_state.assert_called_once_with(model.Job.states.RUNNING)

    @pytest.mark.parametrize("state", [batch_v1.JobStatus.State.QUEUED, batch_v1.JobStatus.State.SCHEDULED])
    def test_queued_or_scheduled_sets_queued_state(self, state):
        runner = _watched_runner()
        runner.batch_client.get_job.return_value = _batch_job_with_state(state)
        js = _job_state()
        assert runner.check_watched_item(js) is js
        assert js.running is True
        js.job_wrapper.change_state.assert_called_once_with(model.Job.states.QUEUED)

    def test_not_found_marks_failed_and_stops(self):
        runner = _watched_runner()
        runner.batch_client.get_job.side_effect = gcp_exceptions.NotFound("missing")
        js = _job_state()
        assert runner.check_watched_item(js) is None
        assert js.running is False
        js.job_wrapper.change_state.assert_called_once_with(model.Job.states.ERROR)
        runner.mark_as_failed.assert_called_once_with(js)

    def test_transient_error_keeps_monitoring(self):
        runner = _watched_runner()
        runner.batch_client.get_job.side_effect = RuntimeError("temporary")
        js = _job_state()
        assert runner.check_watched_item(js) is js
        runner.mark_as_failed.assert_not_called()
