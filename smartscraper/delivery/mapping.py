"""Map extracted records onto an outbound payload.

The MCP console screen edits one of these mapping dicts, and the MCP sink turns
it into the argument object for a tool call on a remote server. The same code
also builds webhook bodies, so one syntax covers both.

Syntax
------
A mapping is a plain dict, loaded from the target's JSON config. Values are:

* ``"$row.<field>"``      one field of the current record, typed (not stringified)
* ``"$run.<attr>"``       one attribute of the run context, e.g. ``$run.extracted_at``
* ``"$index"``            zero-based position of the record inside the batch
* ``"$row"`` / ``"$row.*"``   the whole record as a dict
* ``"$run"`` / ``"$run.*"``   the whole run context as a dict
* ``"text {$row.name}"``  interpolation: the result is always a string
* ``"\\$literal"``        an escaped leading ``$`` produces a literal ``$``
* any other string        a literal
* dict / list             mapped recursively
* ``{"$ref": "...", "default": ...}``   a reference with a fallback value

Keys may use bracket and dot paths so a flat, form-editable config can build a
nested payload::

    {"values[0]": "$run.extracted_at", "values[1]": "$row.name"}
    ->  {"values": ["2026-09-17T09:00:00+00:00", "Widget"]}

Unresolved references yield ``None`` unless ``strict=True``, which raises
``MappingError`` instead. Nothing here touches the network or the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MapContext",
    "MappingError",
    "expand_paths",
    "map_record",
    "map_records",
    "map_value",
    "resolve_ref",
]


class MappingError(ValueError):
    """A reference could not be resolved and the caller asked for strict mode."""


@dataclass(slots=True)
class MapContext:
    """Everything a mapping is allowed to see."""

    row: dict[str, Any] = field(default_factory=dict)
    run: dict[str, Any] = field(default_factory=dict)
    index: int = 0


# A whole-value reference: the string is nothing but the reference.
_WHOLE = re.compile(r"^\$(row|run|index)(?:\.([^\s{}]+))?$")
# An embedded reference inside a larger string: "sold {$row.n} units".
_EMBED = re.compile(r"\{\$(row|run|index)(?:\.([^\s{}]+))?\}")

_MISSING = object()

# "values[0]" / "a.b[2].c" -> ["values", 0] / ["a", "b", 2, "c"]
_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path through dicts, lists and attributes."""
    cur: Any = obj
    for part in path.split("."):
        if cur is None:
            return _MISSING
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            if not part.lstrip("-").isdigit():
                return _MISSING
            idx = int(part)
            if not -len(cur) <= idx < len(cur):
                return _MISSING
            cur = cur[idx]
        else:
            if not hasattr(cur, part):
                return _MISSING
            cur = getattr(cur, part)
    return cur


def resolve_ref(ref: str, ctx: MapContext, *, strict: bool = False) -> Any:
    """Resolve one whole-value reference such as ``$row.price``.

    Returns ``None`` for an unknown field unless ``strict`` is set.
    """
    m = _WHOLE.match(ref.strip())
    if m is None:
        raise MappingError(f"not a reference: {ref!r}")
    return _lookup(m.group(1), m.group(2), ctx, strict=strict)


def _lookup(scope: str, path: str | None, ctx: MapContext, *, strict: bool) -> Any:
    if scope == "index":
        return ctx.index
    source = ctx.row if scope == "row" else ctx.run
    if path in (None, "*"):
        return dict(source)
    value = _dig(source, path or "")
    if value is _MISSING:
        if strict:
            raise MappingError(f"${scope}.{path} is not available")
        return None
    return value


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        import json

        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def map_value(spec: Any, ctx: MapContext, *, strict: bool = False) -> Any:
    """Resolve one mapping value, recursing into dicts and lists."""
    if isinstance(spec, str):
        if spec.startswith("\\$"):
            return spec[1:]
        whole = _WHOLE.match(spec.strip())
        if whole is not None:
            return _lookup(whole.group(1), whole.group(2), ctx, strict=strict)
        if _EMBED.search(spec):
            def sub(m: re.Match[str]) -> str:
                return _stringify(_lookup(m.group(1), m.group(2), ctx, strict=strict))

            return _EMBED.sub(sub, spec)
        return spec
    if isinstance(spec, dict):
        if "$ref" in spec:
            value = map_value(spec["$ref"], ctx, strict=False)
            if value is None and "default" in spec:
                return map_value(spec["default"], ctx, strict=strict)
            if value is None and strict:
                raise MappingError(f"{spec['$ref']!r} is not available")
            return value
        return {k: map_value(v, ctx, strict=strict) for k, v in spec.items()}
    if isinstance(spec, (list, tuple)):
        return [map_value(v, ctx, strict=strict) for v in spec]
    return spec


def _split_path(key: str) -> list[str | int]:
    out: list[str | int] = []
    pos = 0
    for m in _SEGMENT.finditer(key):
        if m.start() > pos and key[pos:m.start()] not in (".",):
            # Something unparseable between segments: treat the key as opaque.
            return [key]
        pos = m.end()
        name, idx = m.group(1), m.group(2)
        out.append(name if name is not None else int(idx))
    if pos != len(key) or not out:
        return [key]
    return out


def expand_paths(flat: dict[str, Any]) -> dict[str, Any]:
    """Turn ``{"values[0]": 1, "values[1]": 2}`` into ``{"values": [1, 2]}``.

    Keys without a dot or bracket pass through untouched. Sparse list indices
    are filled with ``None`` so positions stay stable.
    """
    root: dict[str, Any] = {}
    for key, value in flat.items():
        path = _split_path(key)
        if len(path) == 1 and isinstance(path[0], str):
            root[path[0]] = value
            continue
        _assign(root, path, value)
    return _densify(root)


def _assign(root: dict[str, Any], path: list[str | int], value: Any) -> None:
    cur: Any = root
    for i, seg in enumerate(path):
        last = i == len(path) - 1
        nxt = None if last else path[i + 1]
        if isinstance(seg, int):
            # Lists are held as int-keyed dicts until _densify runs.
            container = cur
            if seg not in container:
                container[seg] = value if last else ({} if isinstance(nxt, str) else {})
            if last:
                container[seg] = value
            else:
                cur = container[seg]
        else:
            if last:
                cur[seg] = value
            else:
                if not isinstance(cur.get(seg), dict):
                    cur[seg] = {}
                cur = cur[seg]


def _densify(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    if node and all(isinstance(k, int) for k in node):
        size = max(node) + 1
        return [_densify(node.get(i)) for i in range(size)]
    return {k: _densify(v) for k, v in node.items()}


def map_record(
    mapping: dict[str, Any],
    row: dict[str, Any],
    run: dict[str, Any] | None = None,
    *,
    index: int = 0,
    strict: bool = False,
) -> dict[str, Any]:
    """Build one outbound payload from one record."""
    ctx = MapContext(row=row or {}, run=run or {}, index=index)
    resolved = {k: map_value(v, ctx, strict=strict) for k, v in (mapping or {}).items()}
    return expand_paths(resolved)


def map_records(
    mapping: dict[str, Any],
    rows: list[dict[str, Any]],
    run: dict[str, Any] | None = None,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Build one payload per record, preserving order."""
    return [map_record(mapping, r, run, index=i, strict=strict) for i, r in enumerate(rows)]
