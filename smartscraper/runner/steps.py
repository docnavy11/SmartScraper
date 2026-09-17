"""Execute a `ScrapeScript`. No model is involved, and none may be.

The whole point of the system is that a run is deterministic: the same script
against the same page gives the same rows, every time, with no tokens spent. The
only non-determinism deliberately present is the rate-limit jitter, which exists
to be polite rather than to be clever.

Three behaviours are worth knowing before reading the code.

**Pagination.** `paginate` closes a block: the steps from the start of the block
to the `paginate` step itself are re-executed once per page. If any `goto` in
that block interpolates the pagination variable, pagination is URL-driven — the
variable is bumped and the block re-runs, and the `next` selector is used only
to answer "is there another page?". Otherwise the `next` link is clicked and the
block is re-run without its `goto` steps, which is what a JS "load more" needs.

**Extraction accumulates.** `extract_list` appends into the named bag rather
than replacing it, so five pages of a product grid leave one bag of all rows and
`emit` sends the lot.

**`custom_python` is code execution.** It is compiled and run unsandboxed in
this process, which is why it logs at warn level with the source recorded before
it runs, and why promoting a version containing one always needs a human.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smartscraper.dsl.models import Field as DslField
from smartscraper.dsl.models import RateLimit, ScrapeScript
from smartscraper.runner.artifacts import RunArtifacts
from smartscraper.runner.block_detect import detect_block
from smartscraper.runner.engines.base import InteractionUnsupported
from smartscraper.runner.errors import Blocked, SelectorNotFound, StepError, ValidationFailed
from smartscraper.runner.locators import (
    Resolution,
    Selector,
    SelectorSyntaxError,
    candidates_of,
    parse_selector,
    resolve_first,
)
from smartscraper.runner.parsing import absolutise, apply_regex, clean_text, coerce
from smartscraper.runner.runlog import RunLogger, null_logger

VAR = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")

#: Steps a static-HTML engine cannot perform. Reaching one is a reason to climb
#: the escalation ladder, not a broken script.
BROWSER_ONLY_OPS: frozenset[str] = frozenset({"fill", "select", "hover", "press", "solve_challenge"})


class MissingVariable(StepError):
    """A `{{name}}` in the script has no value in scope."""


@dataclass
class StepContext:
    """Everything a step can read or change. One per run attempt."""

    script: ScrapeScript
    engine: Any
    log: RunLogger = field(default_factory=null_logger)
    artifacts: RunArtifacts | None = None
    secrets: Mapping[str, str] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)
    bags: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    pages_visited: int = 0
    step_log: list[dict[str, Any]] = field(default_factory=list)
    fallbacks_used: list[dict[str, Any]] = field(default_factory=list)
    custom_python_ran: int = 0
    rng: random.Random = field(default_factory=random.Random)

    @property
    def rate_limit(self) -> RateLimit:
        return self.script.rate_limit

    def bag(self, name: str) -> list[dict[str, Any]]:
        return self.bags.setdefault(name, [])

    def note_fallback(self, res: Resolution, *, where: str) -> None:
        """Record that a primary selector is dead. The repair agent reads this."""
        if not res.is_fallback:
            return
        entry = {"where": where, "primary": res.tried[0], "used": res.used, "index": res.index}
        self.fallbacks_used.append(entry)
        self.log.warn("selector", "primary selector missed; a fallback matched", **entry)

    async def throttle(self, *, reason: str = "") -> float:
        """Random delay inside the script's band. Zero-width band means no wait."""
        lo = max(0.0, float(self.rate_limit.min_delay_s))
        hi = max(lo, float(self.rate_limit.max_delay_s))
        if hi <= 0:
            return 0.0
        delay = self.rng.uniform(lo, hi)
        if delay > 0:
            self.log.debug("rate_limit", f"waiting {delay:.2f}s", seconds=round(delay, 3), reason=reason)
            await asyncio.sleep(delay)
        return delay


# --------------------------------------------------------------------------- vars
def interpolate(template: str, ctx: StepContext) -> str:
    """Replace `{{name}}` from the context's variables.

    An unknown name raises rather than rendering an empty string: a URL silently
    built as `?page=` is exactly the kind of silent-wrong-data failure this
    system exists to catch.
    """
    if not isinstance(template, str) or "{{" not in template:
        return template

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        value = _lookup(name, ctx.vars)
        if value is _MISSING:
            known = ", ".join(sorted(ctx.vars)) or "none"
            raise MissingVariable(f"no value for {{{{{name}}}}} (known: {known})")
        return "" if value is None else str(value)

    return VAR.sub(_sub, template)


_MISSING = object()


def _lookup(name: str, scope: Mapping[str, Any]) -> Any:
    """Dotted lookup, so `{{item.url}}` works inside a `loop`."""
    parts = name.split(".")
    value: Any = scope.get(parts[0], _MISSING)
    for part in parts[1:]:
        if isinstance(value, Mapping):
            value = value.get(part, _MISSING)
        elif value is not _MISSING and hasattr(value, part):
            value = getattr(value, part)
        else:
            return _MISSING
        if value is _MISSING:
            return _MISSING
    return value


# --------------------------------------------------------------------------- entry
async def run_steps(script: ScrapeScript, ctx: StepContext) -> list[dict[str, Any]]:
    """Execute a whole script against an already-open engine."""
    ctx.vars.setdefault("page", 1)
    ctx.vars.setdefault("index", 0)
    await execute_steps(list(script.steps), ctx)
    return ctx.rows


async def execute_steps(steps: Sequence[Any], ctx: StepContext, *, depth: int = 0) -> None:
    """Run a step list. `paginate` re-enters this function for its block."""
    block_start = 0
    i = 0
    while i < len(steps):
        step = steps[i]
        op = getattr(step, "op", "?")
        started = time.monotonic()
        try:
            if op == "paginate":
                await _paginate(step, list(steps[block_start:i]), ctx, depth=depth)
                block_start = i + 1
            else:
                await _dispatch(step, ctx, index=i, depth=depth)
        except (StepError, ValidationFailed, Blocked):
            _record_step(ctx, i, op, started, ok=False)
            raise
        _record_step(ctx, i, op, started, ok=True)
        i += 1


def _record_step(ctx: StepContext, index: int, op: str, started: float, *, ok: bool) -> None:
    ctx.step_log.append(
        {"index": index, "op": op, "ok": ok, "ms": round((time.monotonic() - started) * 1000, 1)}
    )


async def _dispatch(step: Any, ctx: StepContext, *, index: int, depth: int) -> None:
    op = getattr(step, "op", None)
    handler = _HANDLERS.get(op)
    if handler is None:
        raise StepError(f"unknown op {op!r}", op=str(op), index=index)
    if op in BROWSER_ONLY_OPS and not getattr(ctx.engine, "interactive", False):
        raise InteractionUnsupported(op, getattr(ctx.engine, "name", "?"))
    await handler(step, ctx, index, depth)


# --------------------------------------------------------------------------- nav
async def _goto(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    url = interpolate(step.url, ctx)
    await ctx.throttle(reason="goto")
    ctx.log.info("goto", url, url=url, timeout_ms=step.timeout_ms)
    status = await ctx.engine.goto(url, timeout_ms=step.timeout_ms, wait_until=step.wait_until)
    ctx.pages_visited += 1
    await _check_block(ctx, status=status, url=url)
    ctx.log.debug("goto", f"HTTP {status}", status=status, url=ctx.engine.current_url)


async def _check_block(ctx: StepContext, *, status: int | None, url: str) -> None:
    """Classify the document we just received and stop the run if it is a wall."""
    try:
        html = await ctx.engine.content()
    except Exception:
        html = ""
    verdict = detect_block(status, getattr(ctx.engine, "last_headers", {}), html, url=url)
    if not verdict.blocked:
        return
    ctx.log.warn("blocked", verdict.detail or f"blocked: {verdict.reason}", **verdict.as_dict())
    if ctx.artifacts is not None:
        ctx.artifacts.save_html(html, name="blocked.html")
    raise Blocked(verdict.reason or "unknown", status=status, url=url)


async def _wait_for(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    if step.url_contains is not None:
        ok = await ctx.engine.wait_for(None, timeout_ms=step.timeout_ms, url_contains=step.url_contains)
        if not ok:
            raise StepError(f"url never contained {step.url_contains!r}", op="wait_for", index=index)
        return
    sel = _selector(step.selector, op="wait_for", index=index)
    try:
        ok = await ctx.engine.wait_for(sel, state=step.state, timeout_ms=step.timeout_ms)
    except Exception as exc:
        raise SelectorNotFound(
            f"wait_for {step.selector!r} ({step.state}) timed out after {step.timeout_ms}ms: {exc}",
            candidates=[step.selector], op="wait_for", index=index,
        ) from exc
    if not ok:
        raise SelectorNotFound(
            f"wait_for {step.selector!r} ({step.state}) not satisfied",
            candidates=[step.selector], op="wait_for", index=index,
        )
    ctx.log.debug("wait_for", f"{step.selector} is {step.state}", selector=step.selector)


# --------------------------------------------------------------------------- input
async def _click(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    sel = _selector(step.selector, op="click", index=index)
    try:
        await ctx.engine.click(sel, timeout_ms=step.timeout_ms)
    except InteractionUnsupported:
        if step.optional:
            ctx.log.debug("click", "optional click skipped: engine cannot interact",
                          selector=step.selector)
            return
        raise
    except Exception as exc:
        if step.optional:
            ctx.log.debug("click", "optional click missed", selector=step.selector, error=str(exc))
            return
        raise SelectorNotFound(f"click {step.selector!r} failed: {exc}",
                               candidates=[step.selector], op="click", index=index) from exc
    ctx.log.info("click", step.selector, selector=step.selector, url=ctx.engine.current_url)


async def _fill(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    if step.secret:
        if step.secret not in ctx.secrets:
            raise StepError(f"secret {step.secret!r} is not in the secret store",
                            op="fill", index=index)
        value = str(ctx.secrets[step.secret])
        shown = "<secret:" + step.secret + ">"
    else:
        value = interpolate(step.value, ctx)
        shown = value if len(value) < 40 else value[:40] + "…"
    sel = _selector(step.selector, op="fill", index=index)
    try:
        await ctx.engine.fill(sel, value)
    except InteractionUnsupported:
        raise
    except Exception as exc:
        raise SelectorNotFound(f"fill {step.selector!r} failed: {exc}",
                               candidates=[step.selector], op="fill", index=index) from exc
    ctx.log.info("fill", f"{step.selector} = {shown}", selector=step.selector, secret=bool(step.secret))


async def _select(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    sel = _selector(step.selector, op="select", index=index)
    await ctx.engine.select_option(sel, interpolate(step.value, ctx))
    ctx.log.info("select", f"{step.selector} = {step.value}", selector=step.selector)


async def _hover(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    await ctx.engine.hover(_selector(step.selector, op="hover", index=index))
    ctx.log.debug("hover", step.selector, selector=step.selector)


async def _press(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    sel = _selector(step.selector, op="press", index=index) if step.selector else None
    await ctx.engine.press(step.key, selector=sel)
    ctx.log.debug("press", step.key, key=step.key, selector=step.selector)


async def _scroll(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    sel = _selector(step.selector, op="scroll", index=index) if step.selector else None
    await ctx.engine.scroll(to=step.to, selector=sel, times=step.times, pause_ms=step.pause_ms)
    ctx.log.debug("scroll", f"{step.to} x{step.times}", to=step.to, times=step.times)


async def _sleep(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    ctx.log.debug("sleep", f"{step.ms}ms", ms=step.ms)
    await asyncio.sleep(step.ms / 1000)


async def _screenshot(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    if ctx.artifacts is None:
        ctx.log.debug("screenshot", "no artifact directory; skipped")
        return
    target = ctx.artifacts.path(f"{step.name}.png")
    written = await ctx.engine.screenshot(target, full_page=step.full_page)
    if written is not None:
        ctx.artifacts.written[Path(written).name] = Path(written)
    ctx.log.info("screenshot", str(written or "not captured"), path=str(written or ""))


async def _solve_challenge(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    """Not implemented. PLAN.md puts CapMonster and Byparr in Phase 8.

    This raises `Blocked` rather than silently continuing, so the escalation
    ladder does the one thing that can help today: try a better rung.
    """
    ctx.log.warn("solve_challenge", "no solver is wired up; treating as a block",
                 kind=step.kind, max_spend=step.max_spend)
    raise Blocked("captcha", url=ctx.engine.current_url)


# --------------------------------------------------------------------------- extract
def _selector(raw: str | None, *, op: str, index: int) -> Selector:
    if raw is None:
        raise StepError(f"{op} needs a selector", op=op, index=index)
    try:
        return parse_selector(raw)
    except SelectorSyntaxError as exc:
        raise StepError(str(exc), op=op, index=index) from exc


async def _resolve(
    primary: str, fallbacks: Sequence[str] | None, scope: Any, ctx: StepContext,
    *, where: str, minimum: int = 1,
) -> Resolution | None:
    res = await resolve_first(primary, fallbacks, scope.count, minimum=minimum)
    if res is not None:
        ctx.note_fallback(res, where=where)
    return res


async def extract_fields(
    scope: Any, fields: Mapping[str, DslField], ctx: StepContext, *, where: str,
) -> tuple[dict[str, Any], list[str]]:
    """Pull one record out of `scope`. Returns the record and missing required keys.

    A field that matches nothing is `None`, not an exception: the validator's
    `max_null_rate` is the place where that becomes a verdict, and it can only
    do its job if the run completes and produces measurable nulls.
    """
    record: dict[str, Any] = {}
    missing: list[str] = []
    base_url = getattr(ctx.engine, "current_url", "") or ""
    for name, spec in fields.items():
        raw = await _field_value(scope, spec, ctx, where=f"{where}.{name}")
        if raw is not None and spec.regex:
            raw = apply_regex(raw, spec.regex)
        value = coerce(raw, spec.parse)
        if spec.absolute and isinstance(value, str):
            value = absolutise(value, base_url)
        if value is None and spec.default is not None:
            value = spec.default
        if value is None and spec.required:
            missing.append(name)
        record[name] = value
    return record, missing


async def _field_value(scope: Any, spec: DslField, ctx: StepContext, *, where: str) -> str | None:
    res = await _resolve(spec.selector, spec.fallback_selectors, scope, ctx, where=where)
    if res is None:
        return None
    found = await scope.query(res.selector, limit=1)
    if not found:
        return None
    try:
        return await found[0].attr(spec.attr)
    except Exception:
        return None


async def _extract(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    ctx.bag(step.as_)
    scope: Any = ctx.engine
    if step.selector:
        res = await _resolve(step.selector, None, ctx.engine, ctx, where=f"extract[{index}]")
        if res is None:
            raise SelectorNotFound(f"extract scope {step.selector!r} matched nothing",
                                   candidates=[step.selector], op="extract", index=index)
        found = await ctx.engine.query(res.selector, limit=1)
        if not found:
            raise SelectorNotFound(f"extract scope {step.selector!r} matched nothing",
                                   candidates=[step.selector], op="extract", index=index)
        scope = found[0]
    record, missing = await extract_fields(scope, step.fields, ctx, where=f"extract[{index}]")
    if missing:
        raise ValidationFailed(f"extract `{step.as_}` is missing required fields: {', '.join(missing)}")
    ctx.bag(step.as_).append(record)
    ctx.log.info("extract", f"1 record into `{step.as_}`", into=step.as_, fields=list(record))


async def _extract_list(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    # Create the bag before resolving. A page that legitimately has no rows must
    # still leave an empty bag behind, or the `emit` that follows would fail on
    # "nothing named products" and turn an empty result into a script error.
    bag = ctx.bag(step.as_)
    res = await _resolve(
        step.selector, step.fallback_selectors, ctx.engine, ctx, where=f"extract_list[{index}]"
    )
    if res is None:
        tried = candidates_of(step.selector, step.fallback_selectors)
        if step.min_items > 0:
            raise SelectorNotFound(
                f"extract_list found no elements for any of: {', '.join(tried)}",
                candidates=tried, op="extract_list", index=index,
            )
        ctx.log.warn("extract_list", "no elements matched", into=step.as_, tried=tried)
        return
    elements = await ctx.engine.query(res.selector)
    before = len(bag)
    dropped = 0
    for el in elements:
        record, missing = await extract_fields(el, step.fields, ctx, where=f"extract_list[{index}]")
        if missing:
            dropped += 1
            continue
        bag.append(record)
    added = len(bag) - before
    if added < step.min_items:
        raise ValidationFailed(
            f"extract_list `{step.as_}` produced {added} items, min_items is {step.min_items}"
        )
    ctx.log.info(
        "extract_list", f"{added} records into `{step.as_}`",
        into=step.as_, matched=len(elements), added=added, dropped=dropped,
        selector=res.used, total=len(bag),
    )


async def _emit(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    name = step.from_
    if name in ctx.bags:
        rows = ctx.bags[name]
    else:
        value = _lookup(name, ctx.vars)
        if value is _MISSING:
            known = ", ".join(sorted(set(ctx.bags) | set(ctx.vars))) or "nothing"
            raise StepError(f"emit: nothing named {name!r} to emit (have: {known})",
                            op="emit", index=index)
        rows = value if isinstance(value, list) else [value]
    emitted = [r if isinstance(r, dict) else {"value": r} for r in rows]
    ctx.rows.extend(emitted)
    if ctx.artifacts is not None:
        ctx.artifacts.append_records(emitted)
    ctx.log.info("emit", f"{len(emitted)} rows from `{name}`", source=name, rows=len(emitted))


async def _assert(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    if step.selector:
        sel = _selector(step.selector, op="assert", index=index)
        count = await ctx.engine.count(sel)
        if step.min_count is not None and count < step.min_count:
            raise ValidationFailed(
                f"{step.message}: {step.selector!r} matched {count}, expected >= {step.min_count}"
            )
        if step.min_count is None and count < 1:
            raise ValidationFailed(f"{step.message}: {step.selector!r} matched nothing")
    if step.text_contains:
        html = await ctx.engine.content()
        from smartscraper.runner.block_detect import visible_text

        if step.text_contains not in visible_text(html):
            raise ValidationFailed(f"{step.message}: page text does not contain {step.text_contains!r}")
    ctx.log.debug("assert", step.message or "ok", selector=step.selector)


# --------------------------------------------------------------------------- loop
async def _loop(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    if depth > 5:
        raise StepError("loop nesting deeper than 5", op="loop", index=index)
    source = ctx.bags.get(step.over)
    if source is None:
        value = _lookup(step.over, ctx.vars)
        source = value if isinstance(value, list) else None
    if source is None:
        raise StepError(f"loop: nothing named {step.over!r} to iterate", op="loop", index=index)
    items = list(source)[: step.max_iterations]
    ctx.log.info("loop", f"{len(items)} iterations over `{step.over}`",
                 over=step.over, iterations=len(items))
    saved = ctx.vars.get(step.as_, _MISSING)
    saved_index = ctx.vars.get("index", _MISSING)
    try:
        for n, item in enumerate(items):
            ctx.vars[step.as_] = item
            ctx.vars["index"] = n
            await execute_steps(list(step.steps), ctx, depth=depth + 1)
    finally:
        _restore(ctx.vars, step.as_, saved)
        _restore(ctx.vars, "index", saved_index)


def _restore(scope: dict[str, Any], key: str, saved: Any) -> None:
    if saved is _MISSING:
        scope.pop(key, None)
    else:
        scope[key] = saved


# --------------------------------------------------------------------------- paginate
def _block_is_url_driven(block: Sequence[Any], var: str) -> bool:
    """True when a `goto` in the block interpolates the pagination variable."""
    needle = re.compile(r"\{\{\s*" + re.escape(var) + r"(\.[\w.]+)?\s*\}\}")
    return any(
        getattr(s, "op", None) == "goto" and needle.search(getattr(s, "url", "") or "")
        for s in block
    )


async def _paginate(step: Any, block: Sequence[Any], ctx: StepContext, *, depth: int) -> None:
    """Re-run `block` once per additional page, up to the script's caps."""
    var = step.var
    url_driven = _block_is_url_driven(block, var)
    replay = [s for s in block if getattr(s, "op", None) != "goto"] if not url_driven else list(block)
    max_pages = min(step.max_pages, ctx.rate_limit.max_pages_per_run)
    mode = "url" if url_driven else ("load_more" if step.load_more else "next_link")
    ctx.log.info("paginate", f"{mode} pagination, up to {max_pages} pages",
                 mode=mode, max_pages=max_pages, var=var)

    page_no = int(ctx.vars.get(var, 1) or 1)
    for _ in range(max_pages - 1):
        if ctx.pages_visited >= ctx.rate_limit.max_pages_per_run:
            ctx.log.warn("paginate", "max_pages_per_run reached", pages=ctx.pages_visited)
            break
        control = step.next or step.load_more
        res = await _resolve(control, None, ctx.engine, ctx, where="paginate")
        if res is None:
            ctx.log.info("paginate", "no next control on the page; done",
                         page=page_no, control=control)
            break

        before = sum(len(v) for v in ctx.bags.values())
        page_no += 1
        ctx.vars[var] = page_no

        if url_driven:
            await _replay(replay, ctx, depth=depth)
        else:
            await ctx.throttle(reason="paginate")
            try:
                await ctx.engine.click(res.selector)
            except InteractionUnsupported:
                ctx.log.warn("paginate", "engine cannot follow the next control", control=control)
                raise
            except Exception as exc:
                ctx.log.warn("paginate", f"next control failed: {exc}", control=control)
                break
            ctx.pages_visited += 1
            await _check_block(ctx, status=getattr(ctx.engine, "last_status", None),
                               url=ctx.engine.current_url)
            await _replay(replay, ctx, depth=depth)

        added = sum(len(v) for v in ctx.bags.values()) - before
        ctx.log.info("paginate", f"page {page_no}: +{added} records", page=page_no, added=added)
        if step.stop_when_empty and added == 0:
            ctx.log.info("paginate", "page added nothing; stopping", page=page_no)
            break


async def _replay(block: Sequence[Any], ctx: StepContext, *, depth: int) -> None:
    """Re-run a pagination block, tolerating a page that simply has no rows."""
    try:
        await execute_steps(list(block), ctx, depth=depth)
    except ValidationFailed as exc:
        ctx.log.warn("paginate", f"page failed its own check: {exc}")
        raise
    except SelectorNotFound as exc:
        ctx.log.warn("paginate", f"selector missing on this page: {exc}")
        raise


# --------------------------------------------------------------------------- custom
async def _custom_python(step: Any, ctx: StepContext, index: int, depth: int) -> None:
    """Compile and run `def run(page, ctx)` from the script. Unsandboxed.

    Logged loudly and with the source, because the audit trail is the only
    control that exists here: the subprocess boundary and the timeout are the
    containment, and neither stops the code from reading the filesystem.
    """
    ctx.custom_python_ran += 1
    source = step.code or ""
    ctx.log.warn(
        "custom_python",
        f"executing UNSANDBOXED custom_python step #{index} ({len(source)} chars)",
        index=index, timeout_s=step.timeout_s, lines=source.count("\n") + 1, code=source,
    )
    namespace: dict[str, Any] = {"__name__": "smartscraper_custom_step"}
    body = source if _defines_run(source) else _wrap(source)
    try:
        compiled = compile(body, f"<custom_python step {index}>", "exec")
        exec(compiled, namespace)  # noqa: S102 - documented, owner-approved escape hatch
    except SyntaxError as exc:
        raise StepError(f"custom_python failed to compile: {exc}", op="custom_python", index=index) from exc
    func = namespace.get("run")
    if not callable(func):
        raise StepError("custom_python defines no callable `run(page, ctx)`",
                        op="custom_python", index=index)

    page = getattr(ctx.engine, "page", ctx.engine)
    try:
        result = func(page, ctx)
        if asyncio.iscoroutine(result):
            result = await asyncio.wait_for(result, timeout=step.timeout_s)
    except TimeoutError as exc:
        raise StepError(f"custom_python timed out after {step.timeout_s}s",
                        op="custom_python", index=index) from exc
    except StepError:
        raise
    except Exception as exc:
        raise StepError(f"custom_python raised {type(exc).__name__}: {exc}",
                        op="custom_python", index=index) from exc

    if step.as_:
        rows = result if isinstance(result, list) else ([] if result is None else [result])
        normalised = [r if isinstance(r, dict) else {"value": r} for r in rows]
        ctx.bag(step.as_).extend(normalised)
        ctx.log.info("custom_python", f"{len(normalised)} records into `{step.as_}`",
                     into=step.as_, rows=len(normalised))
    else:
        ctx.log.info("custom_python", "step finished", returned=type(result).__name__)


def _defines_run(source: str) -> bool:
    return re.search(r"^\s*(async\s+)?def\s+run\s*\(", source, re.MULTILINE) is not None


def _wrap(source: str) -> str:
    """The DSL documents `code` as the *body* of `run`, so indent and wrap it."""
    indented = "\n".join("    " + line if line.strip() else line for line in source.splitlines())
    return "async def run(page, ctx):\n" + (indented or "    return None")


_HANDLERS = {
    "goto": _goto,
    "wait_for": _wait_for,
    "click": _click,
    "fill": _fill,
    "select": _select,
    "scroll": _scroll,
    "hover": _hover,
    "press": _press,
    "extract": _extract,
    "extract_list": _extract_list,
    "loop": _loop,
    "emit": _emit,
    "sleep": _sleep,
    "screenshot": _screenshot,
    "solve_challenge": _solve_challenge,
    "assert": _assert,
    "custom_python": _custom_python,
}

#: Every op in the DSL has a handler here. Checked at import so a new op added
#: to `dsl.models.OPS` cannot ship with the runner silently ignoring it.
def _check_coverage() -> None:
    from smartscraper.dsl.models import OPS

    missing = set(OPS) - set(_HANDLERS) - {"paginate"}
    if missing:  # pragma: no cover - a failure here is a build error
        raise RuntimeError(f"steps.py has no handler for DSL ops: {sorted(missing)}")


_check_coverage()


def clean(value: str | None) -> str | None:  # pragma: no cover - re-export
    return clean_text(value)
