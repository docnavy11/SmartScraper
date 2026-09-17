"""YAML / JSON / diff / log-line rendering.

The artboards do their syntax colouring in the generator rather than the
browser, so the templates get pre-marked HTML here. Everything returned by this
module is escaped first and then wrapped in spans, so callers may mark it safe.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

_KEY_RE = re.compile(r"^(\s*-?\s*)([A-Za-z_][\w.]*)(:)(.*)$")
_STR_RE = re.compile(r"(&quot;[^&]*&quot;|&#x27;[^&]*&#x27;)")
_NUM_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_LIT_RE = re.compile(r"\b(true|false|null)\b")


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def yaml_line(line: str) -> str:
    """One YAML source line as coloured HTML."""
    if line.strip().startswith("#"):
        return f'<span class="sy-com">{_esc(line)}</span>'
    m = _KEY_RE.match(line)
    if m:
        indent, key, colon, rest = m.groups()
        out = f'<span class="sy-pun">{_esc(indent).replace(" ", "&nbsp;")}</span>'
        out += f'<span class="sy-key">{_esc(key)}</span><span class="sy-pun">{colon}</span>'
        rest_h = _esc(rest)
        rest_h = _STR_RE.sub(r'<span class="sy-str">\1</span>', rest_h)
        rest_h = _NUM_RE.sub(r'<span class="sy-num">\1</span>', rest_h)
        rest_h = _LIT_RE.sub(r'<span class="sy-num">\1</span>', rest_h)
        return out + rest_h.replace("  ", "&nbsp;&nbsp;")
    h = _esc(line).replace(" ", "&nbsp;")
    h = _STR_RE.sub(r'<span class="sy-str">\1</span>', h)
    return f'<span class="t-text-2">{h}</span>'


def yaml_lines(src: str, start: int = 1, marks: set[int] | None = None) -> list[dict[str, Any]]:
    """Numbered, coloured YAML lines for the code viewer."""
    marks = marks or set()
    return [
        {"n": start + i, "html": yaml_line(ln), "mark": (start + i) in marks}
        for i, ln in enumerate(src.split("\n"))
    ]


_JSON_KEY_RE = re.compile(r"(&quot;[\w_]+&quot;)(\s*:)")
_JSON_STR_RE = re.compile(r":(\s*)(&quot;[^&]*&quot;)")
_JSON_NUM_RE = re.compile(r"\b(\d+(?:\.\d+)?|true|false|null)\b")


def json_lines(obj: Any, indent: int = 2) -> list[dict[str, Any]]:
    """Pretty-printed, coloured JSON for the MCP console."""
    src = obj if isinstance(obj, str) else json.dumps(obj, indent=indent, default=str)
    out = []
    for i, ln in enumerate(src.split("\n")):
        h = _esc(ln)
        h = _JSON_KEY_RE.sub(r'<span class="sy-key">\1</span><span class="sy-pun">\2</span>', h)
        h = _JSON_STR_RE.sub(r':\1<span class="sy-str">\2</span>', h)
        h = _JSON_NUM_RE.sub(r'<span class="sy-num">\1</span>', h)
        # .code-src is white-space: pre, so indentation survives without &nbsp;
        out.append({"n": i + 1, "html": h, "mark": False})
    return out


def parse_diff(text: str) -> list[dict[str, Any]]:
    """A unified diff as rows the template can draw.

    Each row is {kind: 'h'|'+'|'-'|' ', old, new, html}. Line numbers come from
    the @@ hunk headers when they are present, which is what `git diff` and
    difflib.unified_diff both emit.
    """
    rows: list[dict[str, Any]] = []
    old_n = new_n = 0
    for raw in (text or "").split("\n"):
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old_n, new_n = int(m.group(1)), int(m.group(2))
            rows.append({"kind": "h", "old": "", "new": "", "html": _esc(raw)})
            continue
        if raw.startswith(("---", "+++", "diff ", "index ")):
            rows.append({"kind": "h", "old": "", "new": "", "html": _esc(raw)})
            continue
        if raw.startswith("-"):
            rows.append({"kind": "-", "old": old_n or "", "new": "", "html": yaml_line(raw[1:])})
            old_n += 1 if old_n else 0
        elif raw.startswith("+"):
            rows.append({"kind": "+", "old": "", "new": new_n or "", "html": yaml_line(raw[1:])})
            new_n += 1 if new_n else 0
        else:
            body = raw[1:] if raw.startswith(" ") else raw
            rows.append(
                {
                    "kind": " ", "old": old_n or "", "new": new_n or "",
                    "html": yaml_line(body) if body else "&nbsp;",
                }
            )
            if old_n:
                old_n += 1
            if new_n:
                new_n += 1
    return rows


_LEVEL_CLASS = {"debug": "debug", "info": "info", "warn": "warn", "error": "err"}


def log_record(line: str) -> dict[str, str]:
    """One JSON log line from runner/runlog.py as display fields.

    A line that is not JSON is still shown: a run whose log got a stray write
    should not blank the log pane.
    """
    line = line.rstrip("\n")
    try:
        rec = json.loads(line)
        if not isinstance(rec, dict):
            raise ValueError
    except Exception:
        return {"ts": "", "level": "info", "tag": "", "msg": line, "cls": "info"}
    ts = str(rec.get("ts", ""))
    if "T" in ts:
        ts = ts.split("T", 1)[1][:8]
    level = str(rec.get("level", "info"))
    tag = str(rec.get("tag", ""))
    msg = str(rec.get("msg", ""))
    extra = " ".join(
        f"{k}={v}" for k, v in rec.items() if k not in {"ts", "level", "tag", "msg", "run_id"}
    )
    if extra:
        msg = f"{msg} · {extra}"
    cls = _LEVEL_CLASS.get(level, "info")
    if tag in {"builder", "repair", "fallback", "agent"}:
        cls = "agent"
    return {"ts": ts, "level": level, "tag": tag, "msg": msg, "cls": cls}


def log_line_html(line: str) -> str:
    """A log line as a single-line HTML fragment, for the SSE stream.

    Server-sent event data may not contain newlines, so this must stay on one
    line.
    """
    r = log_record(line)
    return (
        f'<div class="logline l-{_esc(r["cls"])}">'
        f'<span class="ts">{_esc(r["ts"])}</span>'
        f'<span class="tag">{_esc(r["tag"])}</span>'
        f'<span class="msg">{_esc(r["msg"])}</span></div>'
    )
