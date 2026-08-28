from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from platformdirs import user_state_dir

#: The one on-disk format this server knows how to read, mirroring Scrapy's
#: ``scrapy.utils._remote_control.JOB_FILE_VERSION``.
SUPPORTED_JOB_FILE_VERSION = 1


# match the default path in scrapy.utils._remote_control.job_files_dir()
def default_jobs_dir() -> Path:
    return Path(user_state_dir("scrapy", appauthor=False), "job_files")


class JobError(Exception):
    """A job that cannot be used, with a human-readable reason."""


class JobNotFound(JobError):
    pass


class UnsupportedJobFile(JobError):
    """The file version is unsupported."""


# match what is written by scrapy.utils._remote_control.write_job_file()
@dataclass(frozen=True)
class JobInfo:
    """A job file's parsed contents."""

    job_id: str
    pid: int | None
    port: int | None
    token: str | None
    spider: str | None
    project: str | None
    scrapy_version: str | None
    start_time: float | None
    version: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def supported(self) -> bool:
        """Whether this file can be read by this server."""
        # bool is a subclass of int, so we need to check for it explicitly
        if isinstance(self.version, int) and not isinstance(self.version, bool):
            return self.version == SUPPORTED_JOB_FILE_VERSION
        return False

    @classmethod
    def from_dict(cls, job_id: str, d: dict[str, Any]) -> JobInfo:
        return cls(
            job_id=job_id,
            pid=d.get("pid"),
            port=d.get("port"),
            token=d.get("token"),
            spider=d.get("spider"),
            project=d.get("project"),
            scrapy_version=d.get("scrapy_version"),
            start_time=d.get("start_time"),
            version=d.get("version"),
        )


class JobFile:
    """A wrapper around a single job file at ``path``."""

    def __init__(self, path: Path):
        self.path: Path = path

    @property
    def job_id(self) -> str:
        return self.path.stem

    def read(self) -> JobInfo | None:
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return JobInfo.from_dict(self.job_id, data)


class JobRegistry:
    """A directory of job files; lists and looks them up."""

    def __init__(self, directory: Path | str | None = None):
        self.directory: Path = (
            Path(directory) if directory is not None else default_jobs_dir()
        )

    def list_jobs(self) -> list[JobInfo]:
        """All jobs with a live pid."""
        out: list[JobInfo] = []
        if not self.directory.is_dir():
            return out
        for path in sorted(self.directory.glob("*.json")):
            info = JobFile(path).read()
            if info is None:
                continue
            if info.pid is None or not psutil.pid_exists(info.pid):
                continue
            out.append(info)
        return out

    def get(self, job_id: str) -> JobInfo:
        """One usable job by id, or raise a ``JobError`` saying why not."""
        info = JobFile(self.directory / f"{job_id}.json").read()
        if info is None or info.port is None or info.token is None:
            raise JobNotFound(f"no such job: {job_id}")
        if not info.supported:
            raise UnsupportedJobFile(f"{job_id}: unsupported job file format")
        return info
