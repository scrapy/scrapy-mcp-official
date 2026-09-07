from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from scrapy_mcp.job_files import (
    SUPPORTED_JOB_FILE_VERSION,
    JobFile,
    JobNotFound,
    JobRegistry,
    StaleJobFile,
    UnsupportedJobFile,
    default_jobs_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def registry(tmp_path: Path) -> JobRegistry:
    return JobRegistry(tmp_path / "jobs")


def _write_raw(directory: Path, job_id: str, **values: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "version": 1,
        "pid": os.getpid(),
        "port": 9999,
        "token": "t",
        "spider": "s",
        "project": "p",
        "scrapy_version": "2.17.0",
        "start_time": 1.0,
    }
    data.update(values)
    (directory / f"{job_id}.json").write_text(json.dumps(data))


def test_jobfile_read(tmp_path: Path) -> None:
    _write_raw(tmp_path, "abc", port=12345, token="tok-secret")
    jf = JobFile(tmp_path / "abc.json")
    assert jf.job_id == "abc"

    info = jf.read()
    assert info is not None
    assert info.supported
    assert info.job_id == "abc"
    assert info.port == 12345
    assert info.token == "tok-secret"
    assert info.spider == "s"
    assert info.project == "p"
    assert info.scrapy_version == "2.17.0"
    assert info.base_url == "http://127.0.0.1:12345"
    assert isinstance(info.pid, int)
    assert isinstance(info.start_time, float)


def test_jobfile_read_missing(tmp_path: Path) -> None:
    assert JobFile(tmp_path / "nope.json").read() is None


def test_jobfile_read_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert JobFile(path).read() is None


def test_jobfile_read_non_dict(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]")
    assert JobFile(path).read() is None


@pytest.mark.parametrize(
    "version",
    [
        SUPPORTED_JOB_FILE_VERSION + 1,
        None,
        "1",
        1.0,
        True,
    ],
)
def test_other_versions_are_refused(tmp_path: Path, version: object) -> None:
    _write_raw(tmp_path, "abc", version=version)
    info = JobFile(tmp_path / "abc.json").read()
    assert info is not None
    assert not info.supported


def test_get_refuses_an_unsupported_version(registry: JobRegistry) -> None:
    _write_raw(registry.directory, "future", version=SUPPORTED_JOB_FILE_VERSION + 1)
    with pytest.raises(UnsupportedJobFile):
        registry.get("future")


def test_unsupported_versions_are_still_listed(registry: JobRegistry) -> None:
    _write_raw(registry.directory, "future", version=SUPPORTED_JOB_FILE_VERSION + 1)
    assert [j.job_id for j in registry.list_jobs()] == ["future"]


def test_list_skips_unreadable_files(registry: JobRegistry) -> None:
    _write_raw(registry.directory, "good")
    (registry.directory / "bad.json").write_text("{not json")
    assert [j.job_id for j in registry.list_jobs()] == ["good"]


def test_list_drops_dead_pids(registry: JobRegistry) -> None:
    _write_raw(registry.directory, "alive", pid=os.getpid())
    _write_raw(registry.directory, "dead", pid=999999)
    listed = {j.job_id for j in registry.list_jobs()}
    assert "alive" in listed
    assert "dead" not in listed


def test_get(registry: JobRegistry) -> None:
    _write_raw(registry.directory, "alive", pid=os.getpid(), port=5555, token="tok")
    info = registry.get("alive")
    assert info.base_url == "http://127.0.0.1:5555"
    assert info.token == "tok"


def test_get_missing(registry: JobRegistry) -> None:
    with pytest.raises(JobNotFound):
        registry.get("nope")


@pytest.mark.parametrize("missing", ["port", "token", "pid"])
def test_get_incomplete(registry: JobRegistry, missing: str) -> None:
    _write_raw(registry.directory, "incomplete", **{missing: None})
    with pytest.raises(JobNotFound):
        registry.get("incomplete")


def test_get_refuses_a_dead_pid(registry: JobRegistry) -> None:
    _write_raw(registry.directory, "dead", pid=999999)
    with pytest.raises(StaleJobFile, match=r"pid 999999.*not running"):
        registry.get("dead")


def test_get_reports_an_unsupported_version_before_a_dead_pid(
    registry: JobRegistry,
) -> None:
    _write_raw(
        registry.directory, "future", version=SUPPORTED_JOB_FILE_VERSION + 1, pid=999999
    )
    with pytest.raises(UnsupportedJobFile):
        registry.get("future")


def test_list_empty_when_no_dir(registry: JobRegistry) -> None:
    assert registry.list_jobs() == []


def test_default_jobs_dir() -> None:
    path = default_jobs_dir()
    assert path.name == "job_files"
    assert "scrapy" in path.parts
