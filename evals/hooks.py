from __future__ import annotations

import atexit
import subprocess
import sys
import time
from pathlib import Path

from agent_exam.config import PreRunRequest, PreRunResult

from scrapy_mcp.job_files import JobRegistry

SPIDER = Path(__file__).with_name("fixtures") / "books-crawl" / "books.py"
LOG = Path(__file__).with_name("crawl.log")


def pre_run_hook(request: PreRunRequest) -> PreRunResult:
    """Run the fixture spider for as long as the run lasts, so that every
    attempt finds a live crawl to attach to."""
    crawl = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "scrapy",
            "runspider",
            str(SPIDER),
            "--logfile",
            str(LOG),
        ]
    )
    atexit.register(_stop, crawl)
    for _ in range(120):
        if any(job.pid == crawl.pid for job in JobRegistry().list_jobs()):
            return PreRunResult()
        if crawl.poll() is not None:
            break
        time.sleep(0.5)
    raise RuntimeError(f"The fixture crawl is not attachable, see {LOG}")


def _stop(crawl: subprocess.Popen[bytes]) -> None:
    crawl.terminate()
    try:
        crawl.wait(timeout=30)
    except subprocess.TimeoutExpired:
        crawl.kill()
