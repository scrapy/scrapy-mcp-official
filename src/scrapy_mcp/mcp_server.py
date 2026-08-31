"""Stdio MCP server — the agent-facing edge.

Outcome → ``isError`` mapping (MCPServer: return → isError=false, raise → isError=true):
- envelope ``ok`` / ``error`` / ``timeout`` → return rendered text (the code ran).
- ``compile_error`` and transport failures → raise (distinct messages).
- an unusable job → raise from :func:`status` and :func:`execute`, but only annotate
  in :func:`list_jobs`, where hiding it would hide the very thing worth looking at.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import CrawlClient, RequestError
from .job_files import JobError, JobInfo, JobRegistry
from .reference import EXECUTE_TOOL_DESC, INSPECTION_REFERENCE

app = MCPServer("scrapy-mcp")


def _client(job: JobInfo) -> CrawlClient:
    if job.port is None or job.token is None:
        raise RequestError(f"job {job.job_id} has no port/token, cannot connect")
    return CrawlClient(job.base_url, job.token)


async def _status_of(job: JobInfo) -> dict[str, Any]:
    """A job's status, from the ``/status`` endpoint.

    As a stale job file can outlive its process, it can include a port that was
    reused since the original process exited. We check the pid to catch this.
    """
    data = await _client(job).status()
    if data.get("pid") != job.pid:
        raise RequestError(
            f"pid mismatch (the job file says {job.pid}, the server on port"
            f" {job.port} says {data.get('pid')}) — stale job file, the port was reused"
        )
    return data


def _elapsed(start_time: float) -> str:
    """Format elapsed time."""
    secs = max(0, int(time.time() - start_time))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


async def _health(job: JobInfo) -> str:
    """One-line verdict for a job, for the :func:`list_jobs` listing."""
    # Checked before connecting: in an unrecognized format `port` and `token`
    # cannot be assumed to still mean what they mean here.
    if not job.supported:
        return "unsupported job file format"
    try:
        data = await _status_of(job)
    except RequestError as e:
        return str(e)
    start_time = data.get("start_time")
    if start_time:
        return f"ok, up {_elapsed(start_time)}"
    return "ok"


def _job(job_id: str) -> JobInfo:
    """Look one job up, or raise a ``ValueError`` that MCPServer reports as an error."""
    try:
        return JobRegistry().get(job_id)
    except JobError as e:
        raise ValueError(str(e)) from e


@app.tool(
    description="List attachable live Scrapy crawls and check each one's health.",
    structured_output=False,
)
async def list_jobs() -> str:
    """List attachable live Scrapy crawls (job_id, spider, project, pid) and
    check each one's health by asking its ``/status`` endpoint. Jobs that fail
    the check are still listed, with the reason — a crawl that does not answer
    is usually worth looking into."""
    reg = JobRegistry()
    jobs = reg.list_jobs()
    if not jobs:
        return f"No attachable jobs found (looked in {reg.directory})."
    # Probe concurrently
    healths = await asyncio.gather(*(_health(j) for j in jobs))
    lines = [
        f"- {j.job_id}  spider={j.spider} project={j.project} "
        f"pid={j.pid} Scrapy={j.scrapy_version}  [{h}]"
        for j, h in zip(jobs, healths, strict=True)
    ]
    return "Attachable jobs:\n" + "\n".join(lines)


@app.tool(
    description="Check that a crawl is alive and responsive, and report information about it.",
    structured_output=False,
)
async def status(job_id: str) -> str:
    """Check that a crawl is alive and responsive, and report what it is
    running (spider, project, Scrapy version, pid, uptime).

    Raises if the crawl cannot be reached, with the reason.
    """
    job = _job(job_id)
    try:
        data = await _status_of(job)
    except RequestError as e:
        raise ValueError(f"{job_id}: {e}") from e
    lines = [f"job {job_id}"]
    for label, key in (
        ("spider", "spider"),
        ("project", "project"),
        ("Scrapy", "scrapy_version"),
        ("pid", "pid"),
    ):
        lines.append(f"  {label + ':':<9}{data.get(key)}")
    started = data.get("start_time")
    if started:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
        lines.append(f"  {'started:':<9}{stamp} (up {_elapsed(started)})")
    return "\n".join(lines)


@app.tool(description=EXECUTE_TOOL_DESC, structured_output=False)
async def execute(job_id: str, code: str, timeout_sec: float | None = None) -> str:
    """Send code to execute, return formatted result."""
    # timeout_sec unset or <= 0 means the in-job default applies.
    job = _job(job_id)
    requested = timeout_sec if timeout_sec and timeout_sec > 0 else None
    try:
        env = await _client(job).execute(code, requested)
    except RequestError as e:
        raise ValueError(f"{job_id}: {e}") from e

    if env.get("status") == "compile_error":
        raise ValueError("compile_error:\n" + (env.get("traceback") or ""))

    parts = [f"status={env.get('status')} elapsed={env.get('elapsed_sec')}s"]
    output = env.get("output") or ""
    if output:
        parts.append("--- output ---\n" + output)
    if env.get("output_truncated"):
        parts.append("[output truncated]")
    tb = env.get("traceback")
    if tb:
        parts.append("--- traceback ---\n" + tb)
    if env.get("traceback_truncated"):
        parts.append("[traceback truncated]")
    return "\n".join(parts)


@app.tool(structured_output=False)
def inspection_reference() -> str:
    """Reference for inspecting a live crawl: how it's wired and what's safe to read."""
    return INSPECTION_REFERENCE


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
