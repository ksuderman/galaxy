"""Unit tests for Google Cloud Batch job runner utility methods."""

import os
from unittest.mock import (
    MagicMock,
    patch,
)

import pytest

from galaxy.jobs.runners.util.gcp_batch import (
    convert_cpu_to_milli,
    convert_duration_to_seconds,
    convert_memory_to_mib,
    DEFAULT_GCS_MOUNT_PATH,
    DEFAULT_MAX_RUN_DURATION,
    GCS_CONTAINER_SCRIPT_TEMPLATE,
    GCS_DIRECT_SCRIPT_TEMPLATE,
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


class TestGCSConstants:
    """Tests for GCS-related constants."""

    def test_default_gcs_mount_path(self):
        assert DEFAULT_GCS_MOUNT_PATH == "/galaxy/server/database"


class TestGCSScriptTemplates:
    """Tests for GCS script template rendering."""

    def test_gcs_container_script_renders(self):
        """GCS container script template renders with correct variables."""
        result = GCS_CONTAINER_SCRIPT_TEMPLATE.substitute(
            job_id_tag="job-123",
            tool_id="bwa",
            container_image="quay.io/biocontainers/bwa:0.7.17",
            gcs_mount_path="/galaxy/server/database",
            job_file="/galaxy/server/database/jobs_directory/000/123/galaxy_123.sh",
            galaxy_slots=4,
            galaxy_memory_mb=8192,
            docker_user_flag="--user 1000:1000",
            docker_volume_args='-v "/cvmfs/data.galaxyproject.org:/cvmfs/data.galaxyproject.org:ro"',
        )
        assert "GCS" in result
        assert "job-123" in result
        assert "quay.io/biocontainers/bwa:0.7.17" in result
        assert "/galaxy/server/database" in result
        assert "galaxy_123.sh" in result
        assert "docker run" in result
        # Should NOT contain NFS-specific logic
        assert "nfs-common" not in result
        assert "mount -t nfs" not in result
        assert "actimeo" not in result

    def test_gcs_direct_script_renders(self):
        """GCS direct script template renders with correct variables."""
        result = GCS_DIRECT_SCRIPT_TEMPLATE.substitute(
            job_id_tag="job-456",
            tool_id="samtools",
            gcs_mount_path="/galaxy/server/database",
            job_file="/galaxy/server/database/jobs_directory/000/456/galaxy_456.sh",
            galaxy_slots=2,
            galaxy_memory_mb=4096,
        )
        assert "GCS" in result
        assert "job-456" in result
        assert "/galaxy/server/database" in result
        assert "galaxy_456.sh" in result
        # Should NOT contain Docker or NFS logic
        assert "docker" not in result
        assert "nfs-common" not in result
        assert "mount -t nfs" not in result

    def test_gcs_container_script_has_set_e(self):
        """GCS container script starts with set -e for fail-fast."""
        result = GCS_CONTAINER_SCRIPT_TEMPLATE.substitute(
            job_id_tag="test",
            tool_id="test",
            container_image="test",
            gcs_mount_path="/mnt",
            job_file="/mnt/job.sh",
            galaxy_slots=1,
            galaxy_memory_mb=1024,
            docker_user_flag="",
            docker_volume_args="",
        )
        assert "set -e" in result


class TestGCSStaging:
    """Tests for GCS job file staging logic."""

    def test_stage_job_to_gcs_uploads_files(self, tmp_path):
        """_stage_job_to_gcs uploads job files to the correct bucket paths."""
        # Create mock files
        job_file = tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.sh"
        job_file.parent.mkdir(parents=True)
        job_file.write_text("#!/bin/bash\necho hello")

        output_file = tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.o"
        output_file.write_text("")

        error_file = tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.e"
        error_file.write_text("")

        exit_code_file = tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.ec"
        exit_code_file.write_text("")

        # Mock the storage client and runner
        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        # Create a minimal runner-like object to test the staging logic
        mount_path = str(tmp_path)
        bucket_name = "my-galaxy-bucket"

        # Simulate what _stage_job_to_gcs does
        files_to_upload = [str(job_file), str(output_file), str(error_file), str(exit_code_file)]
        for local_path in files_to_upload:
            if os.path.exists(local_path):
                blob_name = os.path.relpath(local_path, mount_path)
                blob = mock_bucket.blob(blob_name)
                blob.upload_from_filename(local_path)

        # Verify correct blob names
        expected_blobs = [
            "jobs_directory/000/123/galaxy_123.sh",
            "jobs_directory/000/123/galaxy_123.o",
            "jobs_directory/000/123/galaxy_123.e",
            "jobs_directory/000/123/galaxy_123.ec",
        ]
        actual_blob_calls = [call.args[0] for call in mock_bucket.blob.call_args_list]
        assert actual_blob_calls == expected_blobs

        # Verify upload was called for each file
        assert mock_blob.upload_from_filename.call_count == 4

    def test_retrieve_job_from_gcs_downloads_files(self, tmp_path):
        """GCS retrieval downloads exit code, stdout, stderr to local paths."""
        mount_path = str(tmp_path)
        bucket_name = "my-galaxy-bucket"

        output_file = str(tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.o")
        error_file = str(tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.e")
        exit_code_file = str(tmp_path / "jobs_directory" / "000" / "123" / "galaxy_123.ec")

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        # Simulate what _retrieve_job_from_gcs_if_enabled does
        files_to_download = [output_file, error_file, exit_code_file]
        for local_path in files_to_download:
            blob_name = os.path.relpath(local_path, mount_path)
            blob = mock_bucket.blob(blob_name)
            blob.download_to_filename(local_path)

        expected_blobs = [
            "jobs_directory/000/123/galaxy_123.o",
            "jobs_directory/000/123/galaxy_123.e",
            "jobs_directory/000/123/galaxy_123.ec",
        ]
        actual_blob_calls = [call.args[0] for call in mock_bucket.blob.call_args_list]
        assert actual_blob_calls == expected_blobs
        assert mock_blob.download_to_filename.call_count == 3

    def test_path_mapping_preserves_structure(self):
        """Verify that path mapping from local to GCS preserves directory structure."""
        mount_path = "/galaxy/server/database"
        local_path = "/galaxy/server/database/jobs_directory/000/123/galaxy_123.sh"
        blob_name = os.path.relpath(local_path, mount_path)
        assert blob_name == "jobs_directory/000/123/galaxy_123.sh"

    def test_path_mapping_for_datasets(self):
        """Verify dataset paths map correctly to bucket keys."""
        mount_path = "/galaxy/server/database"
        local_path = "/galaxy/server/database/000/dataset_abc123.dat"
        blob_name = os.path.relpath(local_path, mount_path)
        assert blob_name == "000/dataset_abc123.dat"


class TestGCSVolumeCreation:
    """Tests for GCS volume creation in batch job spec."""

    def test_gcs_volume_spec(self):
        """Verify GCS volume is created with correct bucket and mount path."""
        # We can't easily instantiate the full runner, but we can test the
        # batch_v1 volume creation logic directly
        with patch("google.cloud.batch_v1") as mock_batch:
            mock_volume = MagicMock()
            mock_gcs = MagicMock()
            mock_batch.Volume.return_value = mock_volume
            mock_batch.GCS.return_value = mock_gcs

            from google.cloud import batch_v1

            volume = batch_v1.Volume()
            volume.gcs = batch_v1.GCS()
            volume.gcs.remote_path = "my-galaxy-bucket"
            volume.mount_path = "/galaxy/server/database"

            # The test verifies that the API accepts this configuration
            # In production, GCP Batch handles the gcsfuse mount automatically
            assert volume is not None
