from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web

from scrapy_mcp import client, job_files, mcp_server
from scrapy_mcp.reference import INSPECTION_REFERENCE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

TOKEN = "test-token"


class FakeCrawl:
    """A stand-in for the crawl-side HTTP server, driven by the test."""

    def __init__(self) -> None:
        # code string -> envelope to answer with; anything else gets `default`.
        self.envelopes: dict[str, dict[str, Any]] = {}
        self.default = {
            "status": "ok",
            "output": "",
            "traceback": None,
            "elapsed_sec": 0.0,
        }
        # what /status answers with; tests mutate it to model a crawl that has
        # drifted from what its job file claims
        self.status = {
            "pid": os.getpid(),
            "spider": "dummy",
            "project": "testbot",
            "scrapy_version": "2.18.0",
            "start_time": None,
        }
        # how many times /status was actually hit
        self.status_calls = 0
        # a small delay on /status, plus the high-water mark of overlapping
        # calls it lets a test observe
        self.status_delay = 0.0
        self.in_flight = 0
        self.max_in_flight = 0
        # when set, /status answers with this response verbatim, to model a
        # server that answers with something other than JSON-with-200
        self.status_response: web.Response | None = None
        # when set, /status blocks until released — a stand-in for a crawl whose
        # event loop is wedged, without making teardown wait out a real sleep
        self.status_hangs = False
        self._released = asyncio.Event()
        # when set, /execute answers with this response verbatim, to model a
        # port that was reused by something other than a RemoteControl server
        self.execute_response: web.Response | None = None
        # every /execute body received, so tests can assert on the wire payload
        self.requests: list[dict[str, Any]] = []
        self.port: int | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> int:
        app = web.Application()
        app.router.add_get("/status", self._status)
        app.router.add_post("/execute", self._execute)
        self._runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]
        return self.port

    async def stop(self) -> None:
        self._released.set()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    @staticmethod
    def _authed(request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        return header.removeprefix("Bearer ") == TOKEN

    async def _status(self, request: web.Request) -> web.Response:
        self.status_calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if not self._authed(request):
                return web.json_response({"error": "unauthorized"}, status=401)
            if self.status_hangs:
                await self._released.wait()
            if self.status_delay:
                await asyncio.sleep(self.status_delay)
            if self.status_response is not None:
                return self.status_response
            return web.json_response(self.status)
        finally:
            self.in_flight -= 1

    async def _execute(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        self.requests.append(body)
        if self.execute_response is not None:
            return self.execute_response
        return web.json_response(self.envelopes.get(body["code"], self.default))


def write_job_file(
    directory: Path,
    job_id: str,
    *,
    port: int,
    token: str | None = TOKEN,
    pid: int | None = None,
    version: int = 1,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "version": version,
        "pid": os.getpid() if pid is None else pid,
        "port": port,
        "token": token,
        "spider": "dummy",
        "project": "testbot",
        "scrapy_version": "2.17.0",
        "start_time": 1.0,
    }
    (directory / f"{job_id}.json").write_text(json.dumps(data))


@pytest.fixture
async def crawl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[FakeCrawl]:
    """A fake crawl, registered in a temporary jobs directory."""
    monkeypatch.setattr(job_files, "default_jobs_dir", lambda: tmp_path)
    crawl = FakeCrawl()
    port = await crawl.start()
    write_job_file(tmp_path, "job-1", port=port)
    try:
        yield crawl
    finally:
        await crawl.stop()


@pytest.mark.usefixtures("crawl")
async def test_list_jobs_sees_live_job() -> None:
    out = await mcp_server.list_jobs()
    assert "job-1" in out
    assert "dummy" in out
    assert "[ok" in out


async def test_list_jobs_reports_uptime(crawl: FakeCrawl) -> None:
    crawl.status["start_time"] = time.time() - 720
    out = await mcp_server.list_jobs()
    assert "[ok, up 12m]" in out


@pytest.mark.usefixtures("crawl")
async def test_list_jobs_does_not_leak_token() -> None:
    assert TOKEN not in await mcp_server.list_jobs()


async def test_list_jobs_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_files, "default_jobs_dir", lambda: tmp_path)
    assert "No attachable jobs found" in await mcp_server.list_jobs()


async def test_list_jobs_probes_jobs_concurrently(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-2", port=crawl.port)
    crawl.status_delay = 0.05
    await mcp_server.list_jobs()
    assert crawl.max_in_flight == 2


async def test_list_jobs_reports_a_stale_job_file(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    # A live pid, but nothing listening on the port any more.
    await crawl.stop()
    out = await mcp_server.list_jobs()
    assert "job-1" in out  # listed, not hidden
    assert "Connection error" in out


async def test_list_jobs_reports_a_rejected_token(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, token="wrong")
    out = await mcp_server.list_jobs()
    assert "Token rejected" in out


async def test_list_jobs_reports_an_unresponsive_job(
    crawl: FakeCrawl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "STATUS_TIMEOUT", 0.2)
    crawl.status_hangs = True
    out = await mcp_server.list_jobs()
    assert "No response within" in out


async def test_list_jobs_reports_an_unexpected_status(crawl: FakeCrawl) -> None:
    crawl.status_response = web.Response(status=500, text="boom")
    out = await mcp_server.list_jobs()
    assert "Unexpected response status 500" in out
    assert "boom" in out


async def test_list_jobs_reports_a_non_json_response(crawl: FakeCrawl) -> None:
    crawl.status_response = web.Response(text="not json")
    out = await mcp_server.list_jobs()
    assert "Request error" in out


async def test_list_jobs_reports_malformed_json(crawl: FakeCrawl) -> None:
    crawl.status_response = web.Response(
        text="{not json", content_type="application/json"
    )
    out = await mcp_server.list_jobs()
    assert "malformed JSON" in out


async def test_list_jobs_reports_a_missing_token(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, token=None)
    out = await mcp_server.list_jobs()
    assert "job-1" in out  # listed, not hidden, and not blowing up the listing
    assert "no port/token" in out


async def test_list_jobs_reports_a_pid_mismatch(crawl: FakeCrawl) -> None:
    crawl.status["pid"] = os.getpid() + 1
    out = await mcp_server.list_jobs()
    assert "pid mismatch" in out
    assert "port was reused" in out


@pytest.mark.usefixtures("crawl")
async def test_status_reports_what_the_crawl_is_running() -> None:
    out = await mcp_server.status("job-1")
    assert "job-1" in out
    assert "dummy" in out
    assert "testbot" in out
    assert "2.18.0" in out
    assert str(os.getpid()) in out


@pytest.mark.parametrize(
    ("age_sec", "expected"),
    [
        (30, "up 30s"),
        (720, "up 12m"),
        (3720, "up 1h02m"),
    ],
)
async def test_status_reports_uptime(
    crawl: FakeCrawl, age_sec: int, expected: str
) -> None:
    crawl.status["start_time"] = time.time() - age_sec
    out = await mcp_server.status("job-1")
    assert expected in out


@pytest.mark.usefixtures("crawl")
async def test_status_omits_an_unknown_start_time() -> None:
    assert "started:" not in await mcp_server.status("job-1")


@pytest.mark.usefixtures("crawl")
async def test_status_does_not_leak_token() -> None:
    assert TOKEN not in await mcp_server.status("job-1")


@pytest.mark.usefixtures("crawl")
async def test_status_unknown_job_raises() -> None:
    with pytest.raises(ValueError, match="no such job"):
        await mcp_server.status("no-such-job")


async def test_status_on_a_stale_job_file_raises(crawl: FakeCrawl) -> None:
    await crawl.stop()
    with pytest.raises(ValueError, match="Connection error"):
        await mcp_server.status("job-1")


async def test_status_refuses_a_dead_pid(crawl: FakeCrawl, tmp_path: Path) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, pid=999999)
    with pytest.raises(ValueError, match=r"pid 999999.*not running"):
        await mcp_server.status("job-1")
    assert crawl.status_calls == 0


async def test_list_jobs_reports_an_unsupported_version(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, version=99)
    out = await mcp_server.list_jobs()
    assert "job-1" in out
    assert "unsupported job file format" in out
    assert crawl.status_calls == 0


async def test_execute_refuses_an_unsupported_version(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, version=99)
    with pytest.raises(ValueError, match="unsupported job file format"):
        await mcp_server.execute("job-1", "print(1)")
    assert crawl.requests == []


async def test_execute_ok(crawl: FakeCrawl) -> None:
    crawl.envelopes["print(6 * 7)"] = {
        "status": "ok",
        "output": "42\n",
        "traceback": None,
        "elapsed_sec": 0.1,
    }
    out = await mcp_server.execute("job-1", "print(6 * 7)")
    assert "status=ok" in out
    assert "42" in out


async def test_execute_runtime_error_returns_text(crawl: FakeCrawl) -> None:
    crawl.envelopes["boom"] = {
        "status": "error",
        "output": "",
        "traceback": "ValueError: boom\n",
        "elapsed_sec": 0.0,
    }
    out = await mcp_server.execute("job-1", "boom")
    assert "status=error" in out
    assert "boom" in out


async def test_execute_truncation_is_flagged(crawl: FakeCrawl) -> None:
    crawl.envelopes["big"] = {
        "status": "error",
        "output": "x",
        "traceback": "t",
        "elapsed_sec": 0.0,
        "output_truncated": True,
        "traceback_truncated": True,
    }
    out = await mcp_server.execute("job-1", "big")
    assert "[output truncated]" in out
    assert "[traceback truncated]" in out


async def test_execute_compile_error_raises(crawl: FakeCrawl) -> None:
    crawl.envelopes["def (:"] = {
        "status": "compile_error",
        "output": "",
        "traceback": "SyntaxError: invalid syntax\n",
        "elapsed_sec": 0.0,
    }
    with pytest.raises(ValueError, match="compile_error"):
        await mcp_server.execute("job-1", "def (:")


async def test_execute_passes_the_timeout_through(crawl: FakeCrawl) -> None:
    await mcp_server.execute("job-1", "x", timeout_sec=5)
    assert crawl.requests[-1] == {"code": "x", "timeout_sec": 5}


async def test_execute_omits_an_unset_timeout(crawl: FakeCrawl) -> None:
    await mcp_server.execute("job-1", "x")
    assert crawl.requests[-1] == {"code": "x"}


async def test_execute_on_a_stale_job_file_raises(crawl: FakeCrawl) -> None:
    await crawl.stop()
    with pytest.raises(ValueError, match="Connection error"):
        await mcp_server.execute("job-1", "print(1)")


async def test_execute_refuses_a_dead_pid(crawl: FakeCrawl, tmp_path: Path) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, pid=999999)
    with pytest.raises(ValueError, match=r"pid 999999.*not running"):
        await mcp_server.execute("job-1", "print(1)")
    assert crawl.requests == []


async def test_execute_reports_a_rejected_token(
    crawl: FakeCrawl, tmp_path: Path
) -> None:
    assert crawl.port is not None
    write_job_file(tmp_path, "job-1", port=crawl.port, token="wrong")
    with pytest.raises(ValueError, match="Token rejected"):
        await mcp_server.execute("job-1", "print(1)")
    assert crawl.requests == []


async def test_execute_rejects_a_non_envelope(crawl: FakeCrawl) -> None:
    crawl.envelopes["print(1)"] = {"ok": True}
    with pytest.raises(ValueError, match="not a Scrapy crawl"):
        await mcp_server.execute("job-1", "print(1)")


async def test_execute_rejects_an_unknown_status(crawl: FakeCrawl) -> None:
    crawl.envelopes["print(1)"] = {"status": "running", "elapsed_sec": 0.0}
    with pytest.raises(ValueError, match="not a Scrapy crawl"):
        await mcp_server.execute("job-1", "print(1)")


async def test_execute_rejects_a_non_dict_body(crawl: FakeCrawl) -> None:
    crawl.execute_response = web.json_response([1, 2, 3])
    with pytest.raises(ValueError, match=r"Unexpected response body: \[1, 2, 3\]"):
        await mcp_server.execute("job-1", "print(1)")


async def test_execute_rejects_malformed_json(crawl: FakeCrawl) -> None:
    crawl.execute_response = web.Response(
        text="{not json", content_type="application/json"
    )
    with pytest.raises(ValueError, match="malformed JSON"):
        await mcp_server.execute("job-1", "print(1)")


def test_inspection_reference() -> None:
    assert mcp_server.inspection_reference() == INSPECTION_REFERENCE


@pytest.mark.parametrize(
    ("tool_name", "read_only", "open_world"),
    [
        ("list_jobs", True, False),
        ("status", True, False),
        ("inspection_reference", True, False),
        # execute leaves read_only_hint unset, so the destructive default applies
        ("execute", None, True),
    ],
)
async def test_tool_annotations(
    tool_name: str, read_only: bool | None, open_world: bool
) -> None:
    tools = {t.name: t for t in await mcp_server.app.list_tools()}
    hints = tools[tool_name].annotations
    assert hints is not None
    assert hints.read_only_hint is read_only
    assert hints.open_world_hint is open_world


def test_main_runs_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(mcp_server.app, "run", lambda: calls.append(True))
    mcp_server.main()
    assert calls == [True]
