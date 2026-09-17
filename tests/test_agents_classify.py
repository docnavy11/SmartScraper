"""`classify_change` decides whether a repair may promote itself. Cover it hard."""

from __future__ import annotations

import copy

from smartscraper.agents.repair import classify_change, unified_diff
from smartscraper.dsl.models import ScrapeScript
from tests.test_agents_support import script, script_dict


def changed(**mutate) -> ScrapeScript:
    """Build a variant of the sample script from a mutation callback."""
    raw = script_dict()
    mutate["fn"](raw)
    return ScrapeScript.model_validate(raw)


# --------------------------------------------------------------------------- minor
def test_identical_scripts_are_minor_with_no_reasons():
    is_minor, reasons = classify_change(script(), script())
    assert is_minor
    assert reasons == []


def test_changing_a_field_selector_is_minor():
    def mutate(raw):
        raw["steps"][2]["fields"]["price"]["selector"] = ".price-now"

    new = changed(fn=mutate)
    is_minor, reasons = classify_change(script(), new)
    assert is_minor
    assert any("price" in r and "selector" in r for r in reasons)


def test_changing_the_list_container_selector_is_minor():
    def mutate(raw):
        raw["steps"][2]["selector"] = "css=article.product"

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert is_minor
    assert reasons


def test_adding_fallback_selectors_is_minor():
    def mutate(raw):
        raw["steps"][2]["fields"]["name"]["fallback_selectors"] = ["[data-testid=title]"]

    is_minor, _ = classify_change(script(), changed(fn=mutate))
    assert is_minor


def test_changing_a_wait_for_selector_is_minor():
    def mutate(raw):
        raw["steps"][1]["selector"] = "css=article.product"

    is_minor, _ = classify_change(script(), changed(fn=mutate))
    assert is_minor


def test_a_bumped_version_alone_does_not_make_a_change_major():
    is_minor, reasons = classify_change(script(), script(version=9))
    assert is_minor
    assert reasons == []


def test_several_selector_changes_at_once_stay_minor():
    def mutate(raw):
        raw["steps"][2]["selector"] = "css=li.product"
        raw["steps"][2]["fields"]["name"]["selector"] = "h2"
        raw["steps"][2]["fields"]["price"]["fallback_selectors"] = [".amount"]

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert is_minor
    assert len(reasons) == 3


# --------------------------------------------------------------------------- major
def test_adding_a_step_is_not_minor():
    def mutate(raw):
        raw["steps"].insert(2, {"op": "scroll", "to": "bottom", "times": 3})

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("step count changed" in r for r in reasons)


def test_removing_a_step_is_not_minor():
    def mutate(raw):
        del raw["steps"][1]

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("step count changed" in r for r in reasons)


def test_adding_custom_python_is_never_minor():
    def mutate(raw):
        raw["steps"].insert(
            2, {"op": "custom_python", "code": "return page.title()", "as": "title"}
        )

    new = changed(fn=mutate)
    is_minor, reasons = classify_change(script(), new)
    assert not is_minor
    assert "adds a custom_python step" in reasons


def test_custom_python_is_not_minor_even_when_the_step_count_is_unchanged():
    def mutate(raw):
        raw["steps"][1] = {"op": "custom_python", "code": "return 1", "as": "x"}

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert "adds a custom_python step" in reasons


def test_changing_the_body_of_an_existing_custom_python_step_is_not_minor():
    base_raw = script_dict()
    base_raw["steps"].insert(1, {"op": "custom_python", "code": "return 1", "as": "x"})
    old = ScrapeScript.model_validate(base_raw)

    new_raw = copy.deepcopy(base_raw)
    new_raw["steps"][1]["code"] = "import os; return os.listdir('/')"
    new = ScrapeScript.model_validate(new_raw)

    is_minor, reasons = classify_change(old, new)
    assert not is_minor
    assert any("code changed" in r for r in reasons)


def test_adding_a_record_field_is_not_minor():
    def mutate(raw):
        raw["steps"][2]["fields"]["sku"] = {"selector": ".sku", "attr": "text"}

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("'sku' added" in r for r in reasons)


def test_removing_a_record_field_is_not_minor():
    def mutate(raw):
        del raw["steps"][2]["fields"]["price"]

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("'price' removed" in r for r in reasons)


def test_changing_a_field_parser_is_not_minor():
    def mutate(raw):
        raw["steps"][2]["fields"]["price"]["parse"] = "float"

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("parse changed" in r for r in reasons)


def test_changing_the_output_schema_is_not_minor():
    old = script()
    new = script()
    new = new.model_copy(update={"output_schema": {"type": "array", "items": {"type": "object"}}})
    is_minor, reasons = classify_change(old, new)
    assert not is_minor
    assert "output_schema changed" in reasons


def test_changing_a_validation_threshold_is_not_minor():
    old = script()
    new = ScrapeScript.model_validate(script_dict(validation={"min_rows": 50}))
    is_minor, reasons = classify_change(old, new)
    assert not is_minor
    assert "validation changed" in reasons


def test_changing_the_engine_is_not_minor():
    is_minor, reasons = classify_change(script(), script(engine="camoufox"))
    assert not is_minor
    assert "engine changed" in reasons


def test_reordering_steps_is_not_minor():
    def mutate(raw):
        raw["steps"][1], raw["steps"][2] = raw["steps"][2], raw["steps"][1]

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("op changed" in r for r in reasons)


def test_changing_a_goto_url_is_not_minor():
    def mutate(raw):
        raw["steps"][0]["url"] = "https://example.com/catalogue"

    is_minor, reasons = classify_change(script(), changed(fn=mutate))
    assert not is_minor
    assert any("url changed" in r for r in reasons)


# --------------------------------------------------------------------------- nested steps
def nested_pair() -> tuple[dict, dict]:
    raw = {
        "version": 1,
        "steps": [
            {"op": "goto", "url": "https://example.com"},
            {
                "op": "loop",
                "over": "pages",
                "as": "page",
                "steps": [
                    {
                        "op": "extract_list",
                        "selector": ".row",
                        "as": "rows",
                        "fields": {"name": {"selector": "h3", "attr": "text"}},
                    }
                ],
            },
            {"op": "emit", "from": "rows"},
        ],
    }
    return raw, copy.deepcopy(raw)


def test_a_selector_inside_a_loop_is_minor():
    old_raw, new_raw = nested_pair()
    new_raw["steps"][1]["steps"][0]["selector"] = ".record"
    is_minor, reasons = classify_change(
        ScrapeScript.model_validate(old_raw), ScrapeScript.model_validate(new_raw)
    )
    assert is_minor
    assert any("1.0" in r for r in reasons)


def test_a_step_added_inside_a_loop_is_not_minor():
    old_raw, new_raw = nested_pair()
    new_raw["steps"][1]["steps"].append({"op": "sleep", "ms": 500})
    is_minor, reasons = classify_change(
        ScrapeScript.model_validate(old_raw), ScrapeScript.model_validate(new_raw)
    )
    assert not is_minor
    assert any("step count changed" in r for r in reasons)


def test_custom_python_added_inside_a_loop_is_not_minor():
    old_raw, new_raw = nested_pair()
    new_raw["steps"][1]["steps"].append({"op": "custom_python", "code": "return 1"})
    is_minor, reasons = classify_change(
        ScrapeScript.model_validate(old_raw), ScrapeScript.model_validate(new_raw)
    )
    assert not is_minor
    assert "adds a custom_python step" in reasons


# --------------------------------------------------------------------------- diff
def test_unified_diff_shows_the_selector_change_and_the_version_bump():
    def mutate(raw):
        raw["steps"][2]["fields"]["price"]["selector"] = ".price-now"

    new = changed(fn=mutate).model_copy(update={"version": 2})
    diff = unified_diff(script(), new)
    assert "--- v1" in diff
    assert "+++ v2" in diff
    assert "-" in diff and ".price-now" in diff


def test_unified_diff_of_an_unchanged_script_is_empty():
    assert unified_diff(script(), script()) == ""
