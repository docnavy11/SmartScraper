# SmartScraper

An agent writes the scraper once. After that a deterministic runner executes that
script on a schedule with no model in the loop, and the agent is called back only
when a run fails its own validation.

The point is a failure mode that exceptions do not catch. A scrape can succeed
technically and fail completely: every step runs, nothing raises, and the rows
come back quietly wrong because a selector now matches the wrong column. So a run
is judged on its output, not on whether it threw, and a run that fails is held
back from delivery rather than shipped.

## How it works

```
  you: a URL and a sentence          the agent, once
        │                                  │
        ▼                                  ▼
  ┌───────────┐   explores, probes    ┌──────────┐
  │  builder  │──────────────────────▶│  YAML    │   a versioned step list,
  └───────────┘   selectors, tests    │  script  │   not free-form Python
                                      └────┬─────┘
                                           │
        every run after that, no model involved
                                           ▼
  ┌──────────┐    ┌────────────┐    ┌────────────┐    ┌──────────┐
  │  runner  │───▶│ validation │───▶│  delivery  │───▶│ webhook, │
  │ http or  │    │ schema,    │    │ held if it │    │ S3, MCP, │
  │ browser  │    │ nulls,     │    │ failed     │    │ files    │
  └──────────┘    │ row drift  │    └────────────┘    └──────────┘
                  └─────┬──────┘
                        │ failed
                        ▼
                  ┌───────────┐   proposes a new version, tests it,
                  │  repair   │   and waits for you unless the change
                  └───────────┘   is minor and you said auto
```

## What is in the box

- **A step language** rather than generated code. Eighteen operations the agents
  emit and repair. Free-form Python exists as one escape-hatch step that always
  needs human approval, whatever the promotion policy says.
- **An escalation ladder.** Plain HTTP first, then a patched Chromium, then a
  fingerprint-spoofing Firefox. It climbs only when the evidence says the engine
  is the problem, because a page the agent could not read will not read better
  from a stealthier browser.
- **Validation that decides.** Output schema, row-count drift against recent
  runs, per-field null rates, uniqueness. A run that passes every step and fails
  these is a failed run.
- **Delivery that holds.** Six sinks with retry. A failed run reaches only a
  target that opted into provisional rows.
- **An MCP server**, so any agent can list scrapers, trigger runs and read
  results, with freshness on the answer so a consumer can tell provisional data
  from clean data.
- **A web UI** of 23 screens, dense and keyboard-friendly, built to be read at a
  glance rather than admired.

## Running it

```bash
uv venv && uv pip install -e ".[dev]"
python -m playwright install chromium
cp .env.example .env          # set SS_ANTHROPIC_API_KEY at least
smartscraper init-db
smartscraper serve            # http://127.0.0.1:8088
smartscraper worker           # in another shell, for schedules
```

`smartscraper run <name>` executes one scraper in the foreground and prints the
verdict, naming any rule that failed with what was measured and what was
expected.

## Two things to know before pointing it at a site

**It spends money when it thinks.** A build is one agent session and costs real
tokens; runs after that cost nothing. There is a monthly cap and a per-scraper
cap, and both are enforced before a browser opens.

**Scraping is your responsibility.** The tool respects `robots.txt` by default
and rate-limits itself, and both are per-scraper settings you can turn off. That
it *can* fetch a page is not the same as your being entitled to.

## Design notes

`PLAN.md` is the architecture and the reasoning behind each choice, including the
options rejected and why. `UI.md` is the interface spec: the token set, the
screen inventory and the rules the templates follow.

Both record what was measured and what was merely assumed. Where something is a
guess, it says so; the anti-bot marker table in particular is labelled unmeasured
in its own module, because it has never been tested against a corpus of real
challenge pages.

## Status

Built in one sitting and used in anger since. The test suite is comprehensive and
green, and the parts that matter most were verified by running them rather than
by asserting them: a scraper built for a site that refuses headless Chromium, a
run with a third of its prices missing correctly held back from delivery, and
every control on every screen reachable at five viewport sizes.

Unfinished pieces are named rather than hidden: two screens return an honest "not
built" page, re-login needs a browser a person can see, and there is no cron
parser so the schedule column shows the expression rather than a countdown.
