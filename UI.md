# SmartScraper — UI specification

Companion to `PLAN.md`. Style chosen 2026-09-17: **Dense console**, dark-first.
The artboards are built as self-contained HTML so they convert directly into the
Jinja templates named in `PLAN.md` section 9 (`web/templates/`).

## 1. Style system

**Principle.** This is an operations tool. The job of the screen is to let one
person see the state of every scraper at a glance and drop into a failing run in
two clicks. Density beats comfort. Nothing decorative earns space.

**Type.** IBM Plex Sans for UI chrome at 13px. IBM Plex Mono for every number,
identifier, selector, log line and YAML body. Tabular numerals everywhere a
column of figures appears. Fallbacks: `ui-sans-serif, system-ui` and
`ui-monospace, SFMono-Regular, Menlo`.

**Color, dark (default).**

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0B0E14` | page ground |
| `surface` | `#10141D` | panels, table body |
| `surface-2` | `#151A25` | table header, inset blocks |
| `surface-3` | `#1C2331` | hover, selected row |
| `line` | `#212836` | hairlines |
| `line-2` | `#2E3849` | emphasized borders, inputs |
| `text` | `#E3E8F0` | primary |
| `text-2` | `#99A3B5` | secondary |
| `text-3` | `#7F8AA1` | labels, meta (min 11px; 4.5:1 on `surface-3` too) |
| `accent` | `#35C2C2` | primary action, links, **running** |
| `ok` | `#3FB950` | passed |
| `warn` | `#D29922` | drift, needs review |
| `fail` | `#F85149` | failed, blocked |
| `agent` | `#A371F7` | anything an LLM did: builds, repairs, fallback rows |

**Color, light.** Same roles on paper: `bg #F7F8FA`, `surface #FFFFFF`,
`line #E3E7ED`, `text #11151F`, `text-2 #5A6577`, `text-3 #616A7A`,
`accent #0C6F6E`, `ok #177332`, `warn #8F6000`, `fail #CF222E`, `agent #6639BA`.

Five of these moved after the UI was built. Measured against `surface-3`, the
hover and selected-row background, the original `text-3` came out at 4.14:1, so
meta text on a hovered row failed. Darkening it and four light-theme tokens puts
all 32 token-on-surface pairs at 4.5:1 or better in both themes. The numbers here
are the shipped values.

**Rules.** 4px radius, 1px hairlines, no shadows, no gradients. Spacing on a
4px scale. Status is a glyph plus a word, never color alone: `●` passed,
`▲` drift, `✕` failed, `◐` running, `○` queued, `‖` paused. Purple marks
agent-authored artifacts so a deterministic run is never confused with an LLM
one. Tables are 12px mono with 28px rows. Touch targets stay at 44px on
anything reachable by pointer on a small screen.

## 2. Screen inventory

Twenty-three screens, six groups.

**Daily view**
1. **Overview** — health counters, pending repairs, next scheduled runs, live
   activity feed, 24h run strip, spend to date.
2. **Scrapers** — the dense table. Status, engine, schedule, last result, rows,
   next run, spend, enable toggle, per-row actions.
3. **Scraper detail** — one scraper: health, recent runs, active script summary,
   validation rules, delivery targets, profile and proxy, promotion policy.
4. **Records** — query extracted data across runs, with a source column that
   separates script rows from LLM fallback rows, and export.

**Scripts and runs**
5. **Script and versions** — YAML editor, version rail, diff against active,
   test-run, validation rule editor.
6. **Runs** — all runs, filterable by scraper, status, engine, date.
7. **Run detail** — step timeline, validator report, metrics, records preview,
   artifacts, escalation trace, cost.
8. **Run live** — a run in progress: streaming log, current step, browser
   screenshot, cancel.

**Build and repair**
9. **New scraper** — URL, goal, optional schema, engine hints, schedule.
10. **Builder live** — agent transcript, page snapshot, candidate script forming,
    token spend.
11. **Repairs** — queue of candidate versions awaiting approval.
12. **Repair review** — unified diff, validator before and after, agent
    rationale, test-run result, approve or reject.

**Infrastructure**
13. **Network** — login profiles and proxy pools, with per-pool GB and success
    rate.
14. **Delivery** — targets, delivery log with retries, and the MCP endpoint with
    its tool list.
15. **Settings** — models, budgets, engine defaults, captcha and solver config,
    retention, backups.
16. **Style guide** — tokens, both palettes, and every component the templates
    reuse.

**Added after the role review**
17. **Schema and field health** — the output contract, per-field null rate over 30
    days, schema changelog, and who reads it. Without this a renamed field breaks
    a consumer silently.
18. **Coverage** — a scraper by engine success grid with attempts per cell, the
    block-reason breakdown, and suggested starting rungs. The escalation ladder
    on Network is an aggregate and cannot answer which engine works on which site.
19. **MCP console** — try an inbound tool, see the response, and edit the outbound
    field-to-argument mapping that delivery depends on.
20. **Audit log** — who did what, which of it was an agent, plus the inventory of
    scrapers containing a `custom_python` step.
21. **First run** — the empty state. Nothing else in the set shows a fresh install.
22. **Command palette** — keyboard entry to navigation, actions and search.
23. **Phone triage** — 390px wide. Acknowledge, approve or mute from an alert.

## 3. Shell

A 48px top bar carries the wordmark, eight nav items with a badge on Repairs,
and a right cluster with the live run count, month-to-date spend and a theme
toggle. Below it each page has a header strip with title, meta and actions, then
the body. No left sidebar: the horizontal space belongs to the tables.

Three interaction rules came out of the role review and apply everywhere.
Anything in "needs attention" can be acknowledged or muted, so a known problem
stops shouting. Every list that can fail in bulk has row selection and a bulk
action bar. Any view that can show stale data says so in a banner rather than
presenting it as current.

## 4. Where the screens live

- Design canvas (clickable, all 16 side by side):
  https://claude.ai/artifact/W8f4j3MDnMCCb6e2Brnzkc
- Source of each screen: `design/screens/*.dc.html`
- Generator that produced them: `design/build_screens.py`

The generator holds the whole style system as Python constants (palette, type,
`btn`, `chip`, `status`, `table`, `panel`, `topbar`, `pagehead`). Porting to
Jinja means turning those helpers into macros in `web/templates/_ui.html` and
each screen body into a template that extends a shared `base.html`. The nav
links already point at the right route names.
