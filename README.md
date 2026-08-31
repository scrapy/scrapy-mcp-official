# Scrapy MCP Server

[![PyPI version](https://img.shields.io/pypi/v/scrapy-mcp-official.svg)](https://pypi.org/project/scrapy-mcp-official/)
[![Python versions](https://img.shields.io/pypi/pyversions/scrapy-mcp-official.svg)](https://pypi.org/project/scrapy-mcp-official/)
[![Tests](https://github.com/scrapy/scrapy-mcp-official/actions/workflows/tests.yml/badge.svg)](https://github.com/scrapy/scrapy-mcp-official/actions/workflows/tests.yml)
[![Coverage](https://codecov.io/gh/scrapy/scrapy-mcp-official/branch/main/graph/badge.svg)](https://codecov.io/gh/scrapy/scrapy-mcp-official)

This is an MCP server that can connect to live Scrapy crawls to inspect and
control them.

Attach an MCP-capable agent (e.g. **Claude Code**) to a running Scrapy crawl
and inspect, debug, and steer it from the inside — by running async Python
*inside the live crawl process*. It's a structured, agent-oriented successor to
Scrapy's telnet console: the agent writes a snippet, it runs on the crawl's own
event loop, and the output comes back.

## What you can ask it

Once attached, the agent can answer the operational questions you'd otherwise
dig for by hand:

- How much has it scraped, and what's the request error rate?
- What's downloading right now, and from which domains?
- Is it making progress or stuck — and if stuck, why?
- What settings / middlewares / pipelines are actually running?
- Why is it slow? (download delay / concurrency / autothrottle)

It can also explore fixes read-only — e.g. trying candidate selectors against a
response held in memory and diffing the results — and, more experimentally,
patch the running spider.

## How it works

```
agent ──MCP(stdio)──► MCP server ──HTTP+bearer──► RemoteControl extension (live crawl)
                           │
                           └─ job discovery (job files in the Scrapy user state dir)
```

Two pieces are installed separately (but can be installed in the same Python
venv if desired):

- **The Scrapy RemoteControl extension**: ships with Scrapy 2.19.0 and later
  and is enabled by default. While the crawl is running it listens on a random
  localhost port for HTTP requests. You need to know the port number and the
  auth token to connect to it, which you can get from a job file created by the
  extension in the user profile directory.
- **MCP server**: this package. It's a standard stdio MCP server your agent
  (e.g. Claude Code) launches. It discovers jobs by reading job files and
  supports listing them and connecting to them. An agent can use it to send
  code snippets to a crawl process and get back their results.

## Requirements

- Python 3.10+.
- A supported Scrapy crawl, which in turn requires:
  - Scrapy 2.19.0+.
  - Asyncio support is enabled in Scrapy (on by default; off only when
    using a non-default Twisted reactor).
  - The `RemoteControl` Scrapy extension is enabled (on by default).
  - The crawl is running on the same host as the agent (as the communication
    is done via network connections to localhost).
  - The crawl is running under the same user account as the agent (as the job
    files are located in the user profile) or the connection details were given
    to the agent explicitly.

There is no need to install this MCP server into the Python virtual
environment used by Scrapy itself. You can even use `uvx` to run the server
without managing a virtual environment for it, as shown below.

## Setup

You'll need [uv](https://docs.astral.sh/uv/).

Register the MCP server with your agent (e.g. **Claude Code**) — once:

```bash
claude mcp add --scope user scrapy-mcp -- uvx --from scrapy-mcp-official scrapy-mcp
```

Or put it in a project `.mcp.json`:

```json
{
  "mcpServers": {
    "scrapy-mcp": {
      "command": "uvx",
      "args": ["--from", "scrapy-mcp-official", "scrapy-mcp"]
    }
  }
}
```

## Use it on your crawl

No additional configuration is necessary: once a crawl is running you can ask
the agent to list running crawls or to attach to one.

## MCP tools

- **`list_jobs()`** — discover attachable live crawls (job id, spider, project,
  pid), each with a health verdict from its `/status` endpoint. Unhealthy jobs
  are listed with the reason rather than hidden.
- **`status(job_id)`** — check that a crawl is alive and responsive, and report
  what it's running (spider, project, Scrapy version, pid, uptime).
- **`execute(job_id, code, timeout_sec?)`** — run async Python code in a chosen
  crawl and get its output back (prints, status, traceback).
- **`inspection_reference()`** — a Scrapy-internals cheat sheet the agent reads
  before inspecting: object graph, common stats names, scheduler queues, and
  risky patterns to avoid.

## Security

This MCP server is designed to run arbitrary Python code inside your crawl
process. Don't attach an agent to a crawl if you don't want it to be able to
read the data available to that crawl (including e.g. secrets in Scrapy
settings or environment variables) or control the crawl itself (up to stopping
it).
