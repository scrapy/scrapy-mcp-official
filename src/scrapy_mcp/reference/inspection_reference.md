# Inspecting a live Scrapy crawl

Background for navigating and debugging a *running* crawl via `execute`. It describes how
the crawl is wired and what's safe to read — draw your own conclusions about any specific
symptom from the live state. Verified against Scrapy 2.16; if the crawl runs a different
version, read the live objects (the structure is stable, details may shift).

Conceptual data-flow (engine, scheduler, downloader, spiders, item pipeline,
downloader/spider middlewares): https://docs.scrapy.org/en/latest/topics/architecture.md

**Inspect the actual objects and settings — don't assume the defaults.** Middlewares, the
scheduler queue classes, the download path, and exporters can all be replaced or augmented
by settings and add-ons; read what's actually enabled rather than assuming a stock crawl.

## Object graph (live paths from `crawler`)
```
crawler
├── settings · signals · stats · addons · request_fingerprinter
├── spider                      (None until the crawl runs)
├── extensions → .middlewares
└── engine                      (None until the crawl runs)
    ├── running · paused · start_time · spider · pause()/unpause()
    ├── scheduler
    ├── downloader → active · slots · *_concurrency · middleware.middlewares · handlers
    └── scraper    → slot · spidermw.middlewares · itemproc.middlewares
```

## Inspection surface
- **Engine** (`crawler.engine`) — `running`, `paused`, `start_time` (`time.time()-start_time` = uptime), `spider`. Fetch a URL: `await engine.download_async(req)` (`download()` deprecated).
- **Scheduler** (`engine.scheduler`) — `len(...)` = pending backlog, `has_pending_requests()`, `mqs` (memory), `dqs` (disk; `None` unless `JOBDIR`), `df` (dupefilter), `stats`. (See *Scheduler queues*.)
- **Downloader** (`engine.downloader`) — `active: set[Request]` (in-flight); `slots: dict[domain, Slot]`; `total_concurrency`/`domain_concurrency`/`ip_concurrency`/`randomize_delay`; `middleware`; `handlers` (`handlers._schemes`). **Slot**: `concurrency`, `delay`, `randomize_delay`, `active`, `queue`, `transferring`, `lastseen`; `free_transfer_slots()`, `download_delay()`.
- **Scraper** (`engine.scraper`) — `slot` (`None` until spider open): `queue`, `active`, `active_size` (bytes), `itemproc_size`, `max_active_size`, `needs_backout()`; `concurrent_items`.
- **Spider** (`crawler.spider`) — `name`, `start_urls`, `custom_settings`, `settings`, `logger`.
- **Stats** (`crawler.stats`) — `get_stats()`, `get_value(k)`. Common keys: `item_scraped_count`, `downloader/request_count`, `downloader/response_status_count/<code>`, `response_received_count`, `scheduler/enqueued{,/disk,/memory}`, `scheduler/dequeued`, `retry/count`, `downloader/exception_count`, `log_count/ERROR`.
- **Enabled components** (ordered tuples of *instances*): `engine.downloader.middleware.middlewares`, `engine.scraper.spidermw.middlewares`, `engine.scraper.itemproc.middlewares`, `crawler.extensions.middlewares`. To fetch *one* by class: `crawler.get_downloader_middleware(Cls)` / `get_spider_middleware` / `get_item_pipeline` / `get_extension` / `get_addon` → instance or `None`.
- **One-shot snapshot** — `from scrapy.utils.engine import print_engine_status; print_engine_status(crawler.engine)`.

## Scheduler queues
The scheduler holds two parallel structures: `mqs` (in-memory, always) and `dqs` (on-disk,
only when `JOBDIR` is set → survives pause/resume). Each is an **outer priority queue**
wrapping an **inner FIFO/LIFO queue class**:

- Outer (`pqclass`, from `SCHEDULER_PRIORITY_QUEUE`):
  - `ScrapyPriorityQueue` — per-priority sub-queues; dequeues lowest internal priority first (`internal = -request.priority`).
  - `DownloaderAwarePriorityQueue` — **the default since Scrapy 2.14** — round-robins across downloader slots so one domain doesn't starve others. (Incompatible with `CONCURRENT_REQUESTS_PER_IP != 0`.)
- Inner memory (`mqclass`, `SCHEDULER_MEMORY_QUEUE`) and disk (`dqclass`, `SCHEDULER_DISK_QUEUE`): `LifoMemoryQueue`/`FifoMemoryQueue`, `PickleLifoDiskQueue`/`PickleFifoDiskQueue`/`Marshal*DiskQueue` (from `scrapy.squeues`). LIFO ≈ depth-first; FIFO (with `DEPTH_PRIORITY=1`) ≈ breadth-first. Disk queues (de)serialize requests via `request.to_dict()`.
- Start requests have their own queues (`SCHEDULER_START_MEMORY_QUEUE`/`_DISK_QUEUE`); within a priority they're served after regular requests.

Read the live classes rather than assume a default:
- `s = crawler.engine.scheduler`; `s.pqclass.__name__`, `s.mqclass.__name__`, `s.dqclass.__name__`.
- `len(s)`, `len(s.mqs)`, `len(s.dqs) if s.dqs else 0`; `s.dqs is not None` (disk/JOBDIR active).
- Per-priority: iterate `s.mqs.queues` (and `._start_queues`), `s.mqs.curprio`.
- Per-slot (when `DownloaderAwarePriorityQueue`): iterate `s.mqs.pqueues` → each value is a `ScrapyPriorityQueue`.

## Exporters (feed exports)
Feeds are produced by the **`FeedExporter` extension** → `fe = crawler.get_extension(scrapy.extensions.feedexport.FeedExporter)`.
- `fe.feeds: dict[uri, options]` — configured feeds (format, `batch_item_count`, `encoding`, `fields`, `indent`, `store_empty`, `item_classes`, `overwrite`, …).
- `fe.slots: list[FeedSlot]` — active export streams. Per slot: `uri`, `uri_template`, `format`, `batch_id`, `itemcount` (items written so far), `storage`, `feed_options`.
- `fe.exporters` / `fe.storages` — registered format→exporter and scheme→storage classes.
- Formats (`FEED_EXPORTERS_BASE`): `json`, `jsonlines`/`jsonl`/`jl`, `csv`, `xml`, `pickle`, `marshal` → classes in `scrapy.exporters`.
- Storages (`FEED_STORAGES_BASE`): `""`/`file`, `ftp`, `s3`, `gs`, `stdout`.
- Stats: `feedexport/success_count/<Storage>`, `feedexport/failed_count/<Storage>`.

## Memory & live object counts (`trackref`)
Scrapy tracks live instances of `Request`, `Response`, `Selector`, `Item`, `Spider`
(and their subclasses) via `scrapy.utils.trackref` — handy for spotting leaks on long
runs. All read-only:
```python
from scrapy.utils.trackref import print_live_refs, get_oldest, iter_all

print_live_refs()  # live count per class + age of the oldest
get_oldest("HtmlResponse")  # the oldest live instance (inspect .url, .meta, …)
iter_all("Request")  # iterator with every live instance of a class (by class name)
```
Only `object_ref` subclasses are tracked (the classes above). Steadily growing counts —
especially `Response` — usually mean references held too long (a response pinned in
`request.meta`/`cb_kwargs`/a callback closure). For full-heap analysis, Pympler's
`muppy` if it's installed.

## How it behaves (so you don't assume wrong)
- One event loop: synchronous/CPU work in a callback, middleware, pipeline — or in your `execute` — blocks the whole crawl; `await` yields it back.
- Concurrency is enforced per domain/IP via `downloader.slots`, each with its own `delay`. With AutoThrottle on, `slot.delay` is set live: target = `download_latency / AUTOTHROTTLE_TARGET_CONCURRENCY` (default `1.0`), the new delay = average of current and target, clamped to `[DOWNLOAD_DELAY, AUTOTHROTTLE_MAX_DELAY]`, and non-200 responses can only raise it. Per-response latency (connect→headers) is in `response.meta['download_latency']`; `AUTOTHROTTLE_DEBUG=True` logs each adjustment.
- Backpressure: when the scraper slot is full (`active_size ≥ max_active_size` / `needs_backout()`), the engine stops pulling responses and the downloader idles.
- The dupefilter dedups by request fingerprint. `engine`/`spider`/`extensions` are `None` before the crawl starts.
- Components are swappable by settings/add-ons — the scheduler queue class, download handlers, middlewares, and the download path itself may not be the stock ones.

## Read-only discipline
Inspecting means *reading*. A few read-looking methods mutate the crawl — calling them just
to inspect corrupts the run. Default to attribute reads; treat anything that records/pops/
advances state as off-limits unless you intend to mutate.
- `df.request_seen(req)` records the fingerprint, so the crawl then skips that URL. Read-only "seen?" check: `df.request_fingerprint(req) in df.fingerprints`.
- `scheduler.next_request()` dequeues a request (steals it). Use `len(scheduler)` instead.
- `scheduler.enqueue_request()`, `stats.set_value()/inc_value()`, `engine.pause()/unpause()`, and popping `mqs`/`dqs` or slot queues all mutate.
- `slot.download_delay()` isn't corrupting but is non-deterministic (randomizes per call) — read `slot.delay`.

Prefer reading an attribute over calling a method.

## Patching live code (a separate, riskier mode)
Inspecting is read-only; *patching* a running crawl is a separate, less-validated mode. If you
attempt it, two non-obvious hazards — described here so they're recognizable, not as a recipe:

- **Where a queued request's callback comes from depends on the queue.** A request in the
  in-memory queue (`mqs`) holds the actual *bound method* captured when it was created
  (`response.follow(url, callback=self.parse_book)`), so reassigning the method on the spider or
  class does *not* retroactively change requests already queued in memory. A request in a disk
  queue (`dqs`, i.e. `JOBDIR` set) is serialized via `request.to_dict()` with the callback stored
  *by name*, then re-resolved with `getattr(spider, name)` on dequeue (`request_from_dict`) — so
  it *does* pick up a reassigned method. In-flight requests and anything already handed to the
  scraper hold their bound method regardless. (Verified against 2.16.)

- **A code object compiled from a string breaks `inspect.getsource()`, which Scrapy calls on
  every generator callback.** Swapping a function's `__code__` for one compiled inside `execute`
  (`co_filename='<execute>'`, absent from `linecache`) makes `inspect.getsource()` raise
  `OSError: could not get source code`. With `WARN_ON_GENERATOR_RETURN_VALUE` on (the default),
  Scrapy runs `scrapy.utils.misc.warn_on_generator_with_return_value(spider, callback)` on every
  generator callback; it calls `inspect.getsource()` and catches only `IndentationError`, so the
  OSError surfaces as a `spider_exceptions/OSError` on *every* parse — flooding ERROR logs and
  losing that callback's output. (This happened in a real session.)

Live code-patching has non-obvious failure modes across queue types and Scrapy's own
introspection; verify behavior on the live crawl before relying on any approach.

## If the crawl uses scrapy-zyte-api
This integration routes requests through Zyte API, so a stock-Scrapy model misleads.
Detect it: `ZYTE_API_KEY` / `ZYTE_API_TRANSPARENT_MODE` in settings,
`ScrapyZyteAPIDownloadHandler` registered for `http`/`https` in `downloader.handlers`,
`ScrapyZyteAPIDownloaderMiddleware` enabled. What changes:
- **Download path & slots** — Zyte API requests run on a dedicated `zyte-api@<domain>` slot; `CONCURRENT_REQUESTS`/`*_PER_DOMAIN` and AutoThrottle don't govern them the usual way (`ZYTE_API_PRESERVE_DELAY`). So `downloader.slots` for `zyte-api@…` reflect API calls, not direct fetches.
- **Retries/throttling live in the python-zyte-api client** — 429/503/520 are retried by `ZYTE_API_RETRY_POLICY` *before* a Scrapy response exists (exhaustion raises `zyte_api.RequestError`). A low Scrapy `retry/count` is not "no throttling" — look at `scrapy-zyte-api/*` stats (`429`, `throttle_ratio`, `error_ratio`, `mean_response_seconds`).
- **Dedup is param-aware** — `ScrapyZyteAPIRequestFingerprinter` folds the Zyte API output/render params (`browserHtml`, `actions`, `screenshot`, `geolocation`, extraction…) into the fingerprint (ignoring headers/cookies/session id). The same URL with different params is *not* a duplicate.
- **Responses may be browser-rendered** — `browserHtml`/`actions`/`screenshot` give a headless-browser DOM (differs from raw HTML; higher latency). Responses are `ZyteAPITextResponse`/`Xml`/`Json`; the full API result (browserHtml, screenshot, extracted data) is on `response.raw_api_response` (dict).
- **Automatic extraction** — `product`/`article`/… params return structured data (on `response.raw_api_response`, or via scrapy-poet page objects) instead of HTML to parse.
- **Sessions & geolocation** — `ZYTE_API_SESSION_ENABLED` pools manage cookies/IP/bans (see `scrapy-zyte-api/sessions/*` stats); `geolocation` makes requests appear from a region. Bans/session failures are handled inside the integration, not surfaced as ordinary Scrapy errors.

**Reading `scrapy-zyte-api/*` stats** — two families, easily confused:
- *Per attempt (incl. the client's internal retries):* `attempts`, `errors`, `429`, `error_types/<type>`, `exception_types/<class>`, `status_codes/<code>`, and the ratios `error_ratio` (= errors/attempts), `throttle_ratio` (= 429/attempts). These count every try — including transient errors/429s that were retried and then **succeeded** — so a high `error_ratio`/`429` does *not* mean failures.
- *Final outcome (after retries):* `success`, `fatal_errors` (the real failure count), `processed` (= success + fatal_errors), `success_ratio` (= success/processed). Genuine give-up failures show in `fatal_errors` and `error_types/*`.
- So pair them. A request throttled twice then OK ⇒ `attempts=3, 429=2, errors=2, fatal_errors=0, success=1` ⇒ `error_ratio≈0.67` yet `success_ratio=1.0`. (`mean_response_seconds`/`mean_connection_seconds` are timings; `request_args/<arg>` counts how many requests used each param.)

This is verified against scrapy-zyte-api 0.36.0, higher or lower versions can have slightly different settings, stats, or behavior.

## Further reading (Markdown docs)
Pull these only if you need more depth on a topic (llms.txt `.md` renders). For the
docs.scrapy.org links, swap `latest` for the crawl's Scrapy version; scrapy-zyte-api is
versioned separately (its own `0.x` line, not Scrapy's).
- Settings reference — https://docs.scrapy.org/en/latest/topics/settings.md
- Scheduler — https://docs.scrapy.org/en/latest/topics/scheduler.md
- AutoThrottle — https://docs.scrapy.org/en/latest/topics/autothrottle.md
- Memory leaks / `trackref` — https://docs.scrapy.org/en/latest/topics/leaks.md
- Optimization — https://docs.scrapy.org/en/latest/topics/optimize.md
- scrapy-zyte-api — https://scrapy-zyte-api.readthedocs.io/en/latest/llms.txt
