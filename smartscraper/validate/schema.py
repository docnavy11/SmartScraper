"""Row-shape checking and schema inference.

Two jobs:

* `check_schema` judges a row set against `ScrapeScript.output_schema` when the
  script carries one, and against the declared field set when it does not.
* `infer_schema` reads a row set and proposes a JSON Schema. The builder agent
  uses it so a generated script ships with a schema instead of nothing.

The checker implements the JSON Schema subset that matters for scraped records
(type, required, properties, items, enum, additionalProperties, the numeric and
string bounds). It is deliberately self-contained: `jsonschema` is not a
declared dependency of this project. Keywords outside the subset are ignored
rather than guessed at.
"""

from __future__ import annotations

from typing import Any

from smartscraper.contracts import RuleResult
from smartscraper.dsl.models import ScrapeScript

__all__ = ["check_schema", "infer_schema", "schema_errors"]

_MAX_REPORTED = 5


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list | tuple):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _type_ok(value: Any, expected: Any) -> bool:
    wanted = [expected] if isinstance(expected, str) else list(expected)
    actual = _json_type(value)
    if actual in wanted:
        return True
    # JSON Schema: an integer satisfies "number".
    return actual == "integer" and "number" in wanted


def _walk(value: Any, schema: dict[str, Any], path: str, out: list[str]) -> None:
    if not isinstance(schema, dict) or not schema:
        return

    if "type" in schema and not _type_ok(value, schema["type"]):
        out.append(f"{path}: expected {schema['type']}, got {_json_type(value)}")
        return
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in enum")
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: {value!r} != const {schema['const']!r}")

    if isinstance(value, dict):
        props: dict[str, Any] = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value or value.get(name) is None:
                out.append(f"{path}.{name}: required field missing or null")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    out.append(f"{path}.{key}: not allowed by additionalProperties:false")
        for key, sub in props.items():
            if key in value:
                _walk(value[key], sub, f"{path}.{key}", out)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                _walk(item, items, f"{path}[{i}]", out)
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: {len(value)} items, minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: {len(value)} items, maxItems {schema['maxItems']}")
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: length {len(value)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: length {len(value)} > maxLength {schema['maxLength']}")
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {value} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            out.append(f"{path}: {value} <= exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            out.append(f"{path}: {value} >= exclusiveMaximum {schema['exclusiveMaximum']}")


def schema_errors(rows: list[dict[str, Any]], schema: dict[str, Any]) -> list[str]:
    """Every violation found, as readable one-liners. Never raises."""
    errors: list[str] = []
    try:
        if not isinstance(schema, dict) or not schema:
            return errors
        declared = schema.get("type")
        if declared == "array" or (declared is None and "items" in schema):
            _walk(list(rows), schema, "rows", errors)
        else:
            for i, row in enumerate(rows):
                _walk(row, schema, f"row[{i}]", errors)
    except Exception as exc:  # a broken schema must not take the run down
        errors.append(f"schema check aborted: {exc}")
    return errors


def check_schema(rows: list[dict[str, Any]], script: ScrapeScript) -> RuleResult:
    """One rule result for the whole row set.

    With an `output_schema` the rows are checked against it. Without one, the
    rows are checked against the field names the script's extract steps declare,
    which catches a renamed or vanished field.
    """
    if script.output_schema:
        errors = schema_errors(rows, script.output_schema)
        if errors:
            shown = "; ".join(errors[:_MAX_REPORTED])
            extra = f" (+{len(errors) - _MAX_REPORTED} more)" if len(errors) > _MAX_REPORTED else ""
            return RuleResult(
                rule="output_schema",
                passed=False,
                measured=f"{len(errors)} violation(s): {shown}{extra}",
                expected="every row valid against output_schema",
            )
        return RuleResult(
            rule="output_schema",
            passed=True,
            measured=f"{len(rows)} rows valid",
            expected="every row valid against output_schema",
        )

    declared = script.field_names()
    if not declared:
        return RuleResult(
            rule="field_set",
            passed=True,
            measured="no fields declared",
            expected="no check: script declares no extract fields and carries no output_schema",
        )
    missing: set[str] = set()
    for row in rows:
        missing |= {name for name in declared if name not in row}
    if missing:
        return RuleResult(
            rule="field_set",
            passed=False,
            measured=f"missing from some rows: {', '.join(sorted(missing))}",
            expected=f"every row carries: {', '.join(declared)}",
        )
    return RuleResult(
        rule="field_set",
        passed=True,
        measured=f"{len(rows)} rows carry all {len(declared)} declared fields",
        expected=f"every row carries: {', '.join(declared)}",
    )


# --------------------------------------------------------------------------- inference
def _merge_types(seen: set[str]) -> Any:
    concrete = sorted(seen - {"null"})
    if {"integer", "number"} <= set(concrete):
        concrete = [t for t in concrete if t != "integer"]
    if not concrete:
        return "null"
    types: list[str] = concrete + (["null"] if "null" in seen else [])
    return types[0] if len(types) == 1 else types


def infer_schema(rows: list[dict[str, Any]], *, required_threshold: float = 1.0) -> dict[str, Any]:
    """Propose a JSON Schema for a row set.

    A field is `required` when it is present and non-null in at least
    `required_threshold` of the rows (default: all of them).
    """
    total = len(rows)
    properties: dict[str, Any] = {}
    present: dict[str, int] = {}
    types: dict[str, set[str]] = {}
    order: list[str] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if key not in types:
                types[key] = set()
                present[key] = 0
                order.append(key)
            types[key].add(_json_type(value))
            if value is not None and value != "":
                present[key] += 1

    for key in order:
        prop: dict[str, Any] = {"type": _merge_types(types[key])}
        sample = next(
            (r.get(key) for r in rows if isinstance(r, dict) and isinstance(r.get(key), str) and r[key]),
            None,
        )
        if isinstance(sample, str):
            if sample.startswith(("http://", "https://")):
                prop["format"] = "uri"
            elif len(sample) == 10 and sample[4:5] == "-" and sample[7:8] == "-":
                prop["format"] = "date"
        properties[key] = prop

    required = [k for k in order if total and present[k] / total >= required_threshold]
    return {
        "type": "array",
        "items": {
            "type": "object",
            "required": required,
            "properties": properties,
            "additionalProperties": True,
        },
    }
