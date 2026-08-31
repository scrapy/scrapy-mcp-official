Run async Python inside a chosen *live* Scrapy crawl and get its output back.

Before writing code, call the **`inspection_reference`** MCP tool — a separate tool,
not a function available inside the snippet — to learn how the live crawl is wired
and what is safe to read.

Namespace: `crawler` (the live Crawler) + `stash` + Python builtins. Each call runs in a
fresh namespace except `stash`: a dict that persists across calls and is **shared by
every execute call on this job**. Reach the engine/stats/settings via
`crawler.engine`, `crawler.stats`, `crawler.settings`.

Top-level `await` works. To fetch a URL use `await crawler.engine.download_async(request)`.
Output is whatever you `print(...)`; for machine-readable output do `print(json.dumps(...))`.

The code runs on the crawl's event loop: synchronous code pauses the crawl until it
returns (like any sync work on the loop), while `await` yields control back. Calls are not
serialized — while one call is parked on an `await` (e.g. a long observation), other
execute calls and the crawl keep running. Avoid sync-blocking calls (`time.sleep`, heavy
CPU, blocking I/O) — they freeze the crawl and cannot be interrupted.

Each call has a timeout: omit `timeout_sec` for the Scrapy side default (30s); pass a
larger value for long observation loops, up to the server max (600s by default — both
configurable at the Scrapy side). The timeout fires only at an `await` point, so it
can't interrupt sync-blocking code.

Default to read-only inspection.
