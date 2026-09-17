# SmartScraper — Implementation Plan

Status: plan only, no code yet. Written 2026-09-17, decisions finalised the same day.
Decisions made by the owner: Python, single user, self-hosted, web UI from the
start (FastAPI + htmx), proxy support with a vendor list to choose from, promotion
of repaired scripts configurable per scraper (automatic or human-approved), results
in SQLite rows with Parquet export, agents on the Claude Agent SDK for now,
fallback extraction on Sonnet 5, `custom_python` steps allowed in v1.

Evidence labels used in this document:
- **verified** = version / feature read from PyPI, GitHub, or vendor docs on 2026-09-17
- **claim** = vendor, blog, or README statement, not independently measured
- **design** = my recommendation or assumption, untested

---

## 1. What the system does

1. **Build.** The user gives a URL, a goal in plain language, and optionally a
   target output schema. A builder agent (Claude) drives a real browser, explores
   the page, and emits a *scrape script*: a constrained, versioned step list plus
   an output schema, sample output, and validation thresholds.
2. **Run.** A deterministic runner executes the script on a schedule with no LLM
   involved. Each run produces records, a log, and artifacts (screenshot, HTML,
   trace on failure).
3. **Validate.** Every run's output is checked against the schema and against
   sanity thresholds (row-count band, per-field null rate, drift versus recent
   runs). A validation failure is what counts as "error", not only exceptions.
4. **Repair.** On failure the repair agent gets the failing script, the captured
   page, and the validator report, proposes a new script version, test-runs it,
   and either promotes it automatically or parks it for approval (per-scraper
   setting).
5. **Fallback.** While a repair is pending, the run can be completed by LLM
   extraction directly from the captured HTML, flagged as `source=llm_fallback`,
   so downstream consumers keep receiving data. Optional per scraper.
6. **Deliver.** Results go to configured targets: file (JSON/CSV/Parquet),
   webhook, S3, email, chat notification (Slack/Discord/Telegram via apprise),
   or a tool call on an external MCP server.
7. **Expose.** The system itself is an MCP server, so any agent can list
   scrapers, trigger runs, poll status, fetch results, and create new scrapers.
8. **Observe.** A web UI shows scrapers, schedules, runs, live logs, artifacts,
   pending repairs with a diff and approve/reject buttons, and LLM spend.

---

## 2. Architecture

Three long-lived processes plus short-lived run subprocesses, all on one host.

```
┌──────────────────────────────────────────────────────────────────┐
│ app process (uvicorn)                                            │
│   FastAPI  ──  REST API (/api/…)                                 │
│   Jinja + htmx ── server-rendered UI, SSE for live log tails     │
│   fastmcp  ──  MCP server mounted at /mcp (streamable HTTP)      │
└──────────────┬───────────────────────────────────────────────────┘
               │ SQLite (metadata, runs, logs index, LLM usage)
               │ ./data/ (artifacts, results, storage_state, traces)
┌──────────────┴───────────────────────────────────────────────────┐
│ worker process (Huey, SqliteHuey)                                │
│   periodic tasks from cron expressions                           │
│   task: run_scraper(id)  ──▶ spawns subprocess ▶ runner CLI      │
│   task: build_scraper(id), repair_scraper(id), deliver(run_id)   │
└──────────────────────────────────────────────────────────────────┘
optional sidecars (docker): Byparr (Cloudflare solver), Steel (remote browser)
```

Why subprocess per run (design): a hung or crashed browser cannot take the
worker down; stdout/stderr become the run log; a hard wall-clock timeout is a
`kill`, not a hope. Huey's SIGALRM task timeout is the backstop.

Why one Huey worker instead of APScheduler (verified facts, design choice):
Huey 3.4.0 has crontab periodic tasks, retries with backoff, and an optional
stats extra that writes task history to SQLite. APScheduler 3.x has no retry
and no run history; APScheduler 4 is still alpha (4.0.0a6, April 2025) and its
PyPI page says not for production.

---

## 3. Technology choices

All versions verified on PyPI 2026-09-17 unless marked.

| Concern | Choice | Alternative kept in mind | Notes |
|---|---|---|---|
| Runtime | Python 3.12, `uv`, single package `smartscraper` | | Patchright, mcp 2, anthropic 1.x all need >=3.10; Byparr needs 3.14 but runs in its own container |
| Browser engine | **Patchright 1.62.3** (Playwright API, Chromium, tracks upstream) | Camoufox 0.5.6 (Firefox fingerprint) as fallback engine; zendriver 0.16 (AGPL) not used | Vanilla Playwright 1.63 as the "no stealth" engine for friendly sites and for the builder's exploration |
| HTTP engine | **curl_cffi 0.16.3** | primp 2.0.1 | For pages that do not need a browser: cheaper, faster, TLS impersonation |
| Cloudflare challenge | **Byparr v3.0.4** sidecar (FlareSolverr-compatible API) | Scrapling `solve_cloudflare` in-process | Both report intermittent "solved but cookies invalid" issues (verified issue tracker). Expect failures. |
| CAPTCHA | **CapMonster Cloud** (SDK 4.3.0), 2Captcha fallback | | Prices per 1000 (vendor pages, 2026-09-17): Turnstile $1.30, reCAPTCHA v2 $0.60, v3 $0.90. hCaptcha no longer listed by any major vendor. |
| Proxies | Any HTTP/SOCKS5 proxy via Playwright `proxy=` per context; provider-agnostic | Decodo PAYG ($4/GB, 3-day trial) or Evomi/DataImpulse (~$1/GB) | NetNut: do not use (domains seized 2026-07, Krebs). Free proxy lists: not useful against anti-bot targets (claim, Scrapfly) |
| Login / sessions | Playwright `storage_state(indexed_db=True)` per site profile; `launch_persistent_context` for first manual login; pyotp 2.10 for TOTP | | Secrets in an encrypted SQLite column (key from env) — design |
| Scheduler / queue | **Huey 3.4.0** with SqliteHuey + `huey[stats]` | APScheduler 3.11 | Huey license not shown on PyPI; check repo before committing (unverified) |
| Web UI | **FastAPI 0.141 + Jinja2 + htmx 2.x + sse-starlette 3.4** | NiceGUI (rejected by owner) | htmx `sse` extension for live log tails; CodeMirror 6 via CDN for the YAML editor; unified diff rendered server-side with `difflib` into a styled `<pre>`, diff2html JS optional. htmx 4.0 exists but its release date could not be confirmed; start on 2.x — design |
| API | FastAPI 0.141 | | Same process as UI |
| MCP server + client | **fastmcp 4.0.4** on official `mcp` 2.2.0 | official SDK alone | Streamable HTTP mounted on FastAPI, plus stdio entry point for local agents. `fastmcp.Client` for outbound delivery to other MCP servers |
| Agent harness | **Claude Agent SDK** (`claude-agent-sdk` 0.2.154, verified) for builder and repair, model `claude-opus-5`; browser tools registered in-process with `@tool` + `create_sdk_mcp_server` | anthropic SDK beta tool runner later, behind the same `LLMGateway` interface | Owner decision "for now". README says the Claude Code CLI is bundled, a docs page said not; verify at install. Structured JSON from the SDK via its `output_format` json_schema option — verify the exact option name against code.claude.com/docs/en/agent-sdk before Phase 3 |
| Fallback extractor | **`claude-sonnet-5`** through the same gateway, structured output against the scraper's schema | Opus 5 per scraper if quality demands | Prices per MTok (platform.claude.com, 2026-09-17): Opus 5 $5 in / $25 out, cache read $0.50; Sonnet 5 $2 / $10; Haiku 4.5 $1 / $5 |
| Direct API (later) | anthropic 1.6.0, `messages.parse(output_format=PydanticModel)`, `strict: true` tools, prompt caching, server-side fallbacks | | Second gateway backend; not in v1 |
| DB | SQLite, SQLAlchemy 2.0.54 async + aiosqlite, Alembic 1.20 (batch mode) | | SQLModel rejected: still 0.0.x, async unverified |
| Results | JSON rows in SQLite per run by default; Parquet export via DuckDB 1.5.5 as a delivery target | | Design; revisit if a scraper produces >100k rows per run |
| Validation | pydantic 2.13 per record; home-grown per-run metrics table for drift | pandera 0.33 if frame-level rules are wanted later | No library found that does scrape-run drift; build it (verified absence, design) |
| Delivery | httpx2 + tenacity 9.1 (webhooks), boto3 (S3), aiosmtplib (email), apprise 1.13 (chat), fastmcp client (MCP) | gspread (last release 2025-05, maintenance risk) | |
| Config | YAML files in `./scrapers/` are the source of truth; DB mirrors them | | Lets you diff, git, and edit by hand |

Rejected outright: undetected-chromedriver (dead since 2024-02), rebrowser-playwright
(pinned to Playwright 1.52, issues disabled), hrequests and tls-client (stale),
LaVague (dead), Stagehand caching (only works against Browserbase cloud, verified
docs), browser-use OSS (no LLM-free replay).

Considered as a replacement for our own builder: **Skyvern** (AGPL, "code caching"
records and replays without LLM, claim). Not chosen because the script format
must be ours to version, diff, validate, and repair. Playwright MCP's
`browser_start_recording` and `playwright codegen` are useful references for
what a recorded step list looks like.

**Anti-bot honesty note.** The only independent 2026 benchmark found (31
Cloudflare/F5 targets, one IP, one OS) had nodriver, curl_cffi, Patchright, and
Camoufox within a few sites of each other and all ahead of vanilla Playwright.
No independent measurement exists for DataDome, Akamai, or PerimeterX; every
pass claim there is vendor or blog. Plan for an escalation ladder, not a
silver bullet.

---

## 4. The scrape script (DSL)

Versioned YAML/JSON, validated by pydantic. The model emits and repairs this,
never free-form Python.

```yaml
version: 3
engine: browser            # http | browser
stealth: patchright        # none | patchright | camoufox
profile: site-example      # storage_state / persistent context name, optional
proxy: pool-residential-eu # proxy pool name, optional
escalation: [http, patchright, patchright+proxy, camoufox, byparr]   # tried in order on block detection
rate_limit: {min_delay_s: 2, max_delay_s: 6}
steps:
  - {op: goto, url: "https://example.com/products?page={{page}}"}
  - {op: wait_for, selector: "css=.product-card", timeout_ms: 15000}
  - {op: extract_list, selector: "css=.product-card", as: products,
     fields:
       name:  {selector: "h3", attr: text}
       price: {selector: ".price", attr: text, parse: money}
       url:   {selector: "a", attr: href, absolute: true}}
  - {op: paginate, next: "css=a[rel=next]", max_pages: 20, var: page}
  - {op: emit, from: products}
output_schema:                # JSON schema, generated with the script
  type: array
  items: {type: object, required: [name, price, url], properties: {...}}
validation:
  min_rows: 10
  max_rows: 5000
  row_count_band: {relative_to: last_5_runs, tolerance: 0.5}
  max_null_rate: {price: 0.05, url: 0.0}
  unique: [url]
```

Step vocabulary (initial): `goto`, `wait_for`, `click`, `fill`, `select`,
`scroll`, `hover`, `press`, `extract`, `extract_list`, `paginate`, `loop`,
`emit`, `sleep`, `screenshot`, `solve_challenge`, `assert`. Plus `custom_python`
(allowed in v1, owner decision): a step whose body is a Python function
`run(page, ctx) -> Any` executed in the run subprocess with access to the
Playwright page and the step context. It is not sandboxed beyond the subprocess
and its timeout, so a version that adds or changes a `custom_python` step always
requires human approval regardless of the scraper's promotion policy (design).

Locator strategy: prefer role/text/test-id locators, fall back to CSS, with an
optional `fallback_selectors` list per field so one broken selector does not
fail the run (design).

---

## 5. Agents

Builder and repair run on the Claude Agent SDK with `claude-opus-5`; the fallback
extractor runs on `claude-sonnet-5`. All calls go through one `LLMGateway`
interface (`run_agent(tools, prompt, schema)`, `extract(schema, text)`), so the
backend can be swapped to the anthropic SDK tool runner later without touching
the agents. Agent SDK options used: `mcp_servers` with our in-process browser
server, `allowed_tools` restricted to those tools (no Bash, no file tools),
`can_use_tool` as the approval hook for `custom_python` proposals, `max_turns`
and a per-scraper budget check in a hook. Usage from each result message is
written to `llm_usage` with the scraper id, so the UI can show spend per
scraper and enforce a monthly budget. Whether the SDK reports token usage per
call in the shape needed here is unverified; confirm in Phase 3.

Alternative for the builder's browser tools: Microsoft's Playwright MCP server
(Node) plugged in as an external `mcp_servers` entry. Rejected for v1 because it
needs Node and gives no control over snapshot trimming or `propose_script`.

**Builder.** Tools exposed to the model: `navigate`, `snapshot` (accessibility
tree plus trimmed HTML of the viewport), `click`, `fill`, `scroll`,
`extract_probe` (run a candidate selector, return matches and sample values),
`propose_script` (validated against the DSL schema, then executed once; the
model gets the validator report back), `finish`. The builder ends with a script
that passed a live run and produced at least the minimum rows. It also stores
the captured HTML as a fixture so future repairs can be tested offline first.

**Repair.** Input: current script, validator report, HTML + screenshot +
accessibility snapshot from the failed run, last-known-good sample output.
Tools: same as builder plus `diff_against_current`. Output: a new script
version with a change summary. The runner test-runs it live; only a passing
version goes to the promotion step. Promotion policy per scraper: `auto`,
`manual`, or `auto_if_minor` (design: minor = only selectors changed, no new
steps, row count within band).

**Fallback extractor.** Single structured-output call on Sonnet 5 through the
gateway, over the captured HTML reduced to text plus a trimmed DOM, chunked if
large, validated against the scraper's output schema with pydantic. Flagged
rows. Per-scraper switch and per-run cost cap.

**Block detection** (deterministic, no LLM): HTTP 403/429/503, known challenge
DOM markers (Cloudflare, DataDome, PerimeterX, Akamai), captcha iframes, empty
body with a script-only page. Triggers escalation within the run before the
run is marked failed.

---

## 6. Data model (SQLite)

- `scraper` — id, name, yaml_path, goal, url, schedule (cron), promotion_policy,
  fallback_enabled, budget_usd_month, proxy_pool, profile, enabled
- `script_version` — scraper_id, version, yaml, created_by (builder|repair|human),
  change_summary, status (candidate|active|rejected|retired), test_run_id
- `run` — scraper_id, script_version, started/finished, status
  (queued|running|passed|validation_failed|error|repaired|fallback),
  row_count, engine_used, escalation_level, proxy_used, cost_usd,
  log_path, artifact_dir
- `run_metrics` — run_id, per-field null rate, distinct count, row_count
- `record` — run_id, json, source (script|llm_fallback), hash for dedupe
- `delivery` — run_id, target, status, attempts, last_error
- `delivery_target` — scraper_id, type (file|webhook|s3|email|apprise|mcp), config
- `repair` — scraper_id, run_id, candidate_version, status
  (pending_approval|approved|rejected|auto_promoted), agent_transcript_path
- `llm_usage` — scraper_id, run_id, agent, model, input/output/cache tokens, usd
- `profile` — name, storage_state_path, user_data_dir, notes
- `proxy_pool` — name, entries (server, username, password enc), rotation
  (round_robin|sticky_per_run), sticky_ttl
- `secret` — key, value enc

Artifacts on disk: `data/runs/<run_id>/{log.txt, page.html, screen.png,
trace.zip (failures only), records.jsonl}`.

---

## 7. Web UI pages

1. **Scrapers** list: status, next run, last run result, spend this month.
2. **Scraper detail**: YAML editor (codemirror), version history, schedule,
   delivery targets, proxy/profile, promotion policy, run-now, build/rebuild.
3. **Runs**: filterable table; run detail with live log tail (websocket),
   metrics, records preview, artifacts, "open trace".
4. **Repairs**: pending candidates with unified diff, validator report before
   and after, approve/reject, "test again".
5. **Builder**: new-scraper wizard (URL + goal + optional schema), live agent
   transcript, final script preview, save.
6. **Profiles & proxies**: manage login profiles (launch headed browser for
   manual login, save storage state), proxy pools, secrets.
7. **Settings & spend**: API key status, budgets, LLM usage charts, MCP
   endpoint info and token.

---

## 8. MCP server tools (inbound)

`list_scrapers`, `get_scraper`, `create_scraper(url, goal, schema?)` (runs the
builder, returns job id), `run_scraper(id, wait?)`, `get_run(run_id)`,
`get_results(scraper_id, run_id?|latest, limit, offset)`, `search_results`,
`approve_repair(repair_id)`, `get_pending_repairs`. Resources:
`scraper://<id>/script`, `run://<id>/log`. Auth: bearer token from config
(fastmcp token verification). Transports: streamable HTTP under the app, and a
`smartscraper mcp --stdio` entry point.

Outbound MCP delivery target: `{server_url|command, tool_name, arg_template}`;
records are passed as the tool argument, in batches. Uses `fastmcp.Client`.

---

## 9. Repository layout

```
smartscraper/
  pyproject.toml            # uv, ruff, pytest
  smartscraper/
    dsl/        models.py (pydantic), schema.json, migrate.py
    runner/     engine_http.py, engine_browser.py, steps.py, locators.py,
                block_detect.py, escalation.py, cli.py (subprocess entry)
    validate/   schema.py, metrics.py, drift.py
    agents/     gateway.py (LLMGateway; agent_sdk backend now, anthropic later),
                tools_browser.py (in-process MCP server), builder.py, repair.py,
                fallback_extract.py, prompts/
    scheduler/  huey_app.py, tasks.py
    delivery/   base.py, file.py, webhook.py, s3.py, email.py, apprise.py, mcp.py
    mcp/        server.py
    web/        app.py (FastAPI), templates/ (Jinja + htmx), static/, routes/, api/
    db/         models.py, session.py, migrations/ (alembic)
    profiles.py, proxies.py, secrets.py, config.py
  scrapers/     *.yaml (source of truth)
  data/         runtime (gitignored)
  docker/       compose.yml (app, worker, byparr optional, steel optional)
  tests/        dsl, runner (against local fixture HTML served by a tiny server),
                validate, agents (recorded), e2e
```

---

## 10. Phases

Each phase ends with something runnable. Estimates are rough working-days for
one developer with an AI pair; not measured against anything.

**Phase 0 — Skeleton (1–2 days).** Repo, uv, config, SQLite + Alembic, run
subprocess CLI that executes a hand-written YAML script against a local fixture
site with vanilla Playwright, records to jsonl. Tests for the DSL models.

**Phase 1 — Deterministic runner (3–5 days).** Full step vocabulary, locator
fallbacks, pagination, HTTP engine via curl_cffi, Patchright engine, per-run
artifacts, structured log, screenshot/trace on failure, block detection,
escalation ladder, proxy per context, profiles with storage_state.
Milestone: three real sites scraped from YAML you wrote by hand.

**Phase 2 — Validation (1–2 days).** Schema validation, metrics table, drift
rules, validator report format. Runs get `validation_failed` status.

**Phase 3 — Builder agent (4–6 days).** Gateway with the Agent SDK backend,
in-process browser tool server, snapshot trimming, `propose_script` with live test, fixture capture, usage
logging, budget enforcement. Milestone: type URL + goal on the CLI, get a
working YAML that passes Phase 2 validation on three sites.

**Phase 4 — Repair + fallback (3–4 days).** Repair agent, candidate versions,
promotion policies, fallback extraction with structured outputs. Milestone:
break a selector in a fixture, watch it self-repair; break the page structure,
watch fallback deliver flagged rows and a candidate appear for approval.

**Phase 5 — Scheduling + delivery (2–3 days).** Huey worker, cron, retries,
delivery targets with retry and status, apprise notifications on failure and
on pending repair.

**Phase 6 — Web UI (5–7 days).** Pages in section 7 as Jinja templates with
htmx partial swaps. Live log via SSE. Server-rendered diff view for repairs.
Manual-login flow for profiles.

**Phase 7 — MCP (2 days).** Inbound server with token auth, stdio mode,
outbound MCP delivery target.

**Phase 8 — Hardening (ongoing).** Byparr sidecar, CapMonster integration
behind `solve_challenge`, Camoufox engine, docker compose, backups of SQLite
and `data/`, per-domain rate limits, robots.txt respect toggle.

Total to a usable v1 (phases 0–7): roughly 4–6 weeks of focused work. Rough
estimate, not measured.

---

## 11. Risks and open questions

- **Anti-bot outcomes are unmeasured.** Build the ladder, measure per site,
  keep the hosted-browser escape hatch (Steel self-hosted, or ZenRows /
  Browserbase paid) as a last rung.
- **Silent wrong data.** The drift rules are the defence; tune thresholds per
  scraper after the first ten runs. Consider a periodic "LLM audit" that
  samples a run and compares against a fresh LLM extraction (design, costs
  tokens).
- **Builder quality on JS-heavy or infinite-scroll sites.** Expect iteration on
  the snapshot format; the accessibility tree plus trimmed HTML is the starting
  point, not a known-good answer.
- **Huey license** not shown on PyPI; confirm on GitHub before Phase 5.
- **Agent SDK fit.** The SDK is a Claude Code harness; running it with only our
  browser tools and no filesystem tools is supported by `allowed_tools` on paper
  but untested here. Structured output option name, per-call usage reporting,
  and CLI bundling all need a check in Phase 3. The gateway keeps the exit
  cheap if it does not fit.
- **Legal/ToS.** Per-scraper `respect_robots` and rate limits are the tools;
  policy is the owner's call.
- **Secrets at rest.** Env-key-encrypted columns are single-user grade, not a
  vault.
- **`custom_python` is code execution.** A repair agent can propose it; the
  mandatory human approval is the only gate. Keep it that way.
- **Cost.** Builder runs are the expensive part (many tool turns with page
  snapshots). Prompt caching on the system prompt and tool list, and trimming
  snapshots aggressively, are the levers. Expect a few dollars per build at
  Opus 5 rates; not measured.

---

## 12. Decisions made (2026-09-17)

1. UI: FastAPI + Jinja + htmx.
2. Results: SQLite rows, Parquet as an export target.
3. Fallback extractor: Sonnet 5. Agents on the Claude Agent SDK for now.
4. Proxy vendor: owner picks from the list below; the runner is vendor-agnostic.
5. `custom_python` steps: allowed in v1, always human-approved.

---

## 13. Proxy vendor list

Residential prices as shown on vendor pages on 2026-09-17, per GB. Sticky
sessions matter for logged-in scrapes (same IP across a run). Trial matters for
measuring which sites actually pass, since no independent data exists.

| Vendor | Entry price | Volume price | Sticky session | Trial | Notes |
|---|---|---|---|---|---|
| Decodo (ex-Smartproxy) | $4 PAYG, $3.75 at 3 GB | $2.75 at 100 GB | minutes to days | 3 days / 100 MB | Well-documented, ZIP/ASN targeting. Suggested first trial |
| Evomi | $0.99 PAYG (15 GB min) | $0.49 at 100 GB/mo | 30 min default, up to 24 h | 1 day | Cheapest per GB with a real trial |
| DataImpulse | $1 ($5 for 5 GB) | $0.80 at 1 TB | yes | none | Cheap, no trial |
| PacketStream | $1 flat, $50 min | same | 60 min | on request | Peer-to-peer network |
| Webshare | $3.50 (1 GB) | $2.25 at 100 GB | not stated | 10 free datacenter proxies | Datacenter tier useful for friendly sites |
| SOAX | $5 sandbox, $3 at $200/mo | $2.20 at $500/mo | not stated | free sandbox | |
| Oxylabs | $6 (5 GB) | $4 at 125 GB | 24 h | once | Enterprise-leaning |
| IPRoyal | $7.35 (1 GB) | $5.51 at 10 GB | up to 7 days | via Google login | Longest sticky window |
| Bright Data | $8 list ($4 with coupon) | $3 at $999/mo | yes | yes, KYC | Largest pool, KYC required |
| NetNut | — | — | — | — | Do not use: domains seized 2026-07-02, tied to a botnet (Krebs on Security) |

Not on the list on purpose: free proxy lists (free-proxy, swiftshadow,
proxybroker2). Datacenter ranges are blocked before Cloudflare's JS challenge
even starts (claim, Scrapfly 2026-08-31), and free lists were not measured.

Runner integration is identical for all vendors: `browser.new_context(proxy=
{"server": "http://host:port", "username": ..., "password": ...})`, one context
per proxy, rotation `round_robin` or `sticky_per_run` from the `proxy_pool`
table. Sticky-session username syntax differs per vendor and was not fetched;
store it as a per-pool template string.
