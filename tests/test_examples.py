"""Whatever ships in examples/ has to still work.

An example is the first thing anyone runs and the last thing anyone remembers to
maintain. A broken one is worse than none: it teaches a shape that does not
parse, and it is read as the project's own idea of good practice.

These checks are offline. They do not fetch the site, so they say nothing about
whether the selectors still match; they say the file is a valid script, that it
carries the things an example ought to demonstrate, and that it does not quietly
ship anything dangerous.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smartscraper.dsl.models import ScrapeScript

EXAMPLES = sorted((Path(__file__).resolve().parent.parent / "examples").glob("*.yaml"))


def test_there_is_at_least_one_example():
    assert EXAMPLES, "examples/ is empty; the README points people at it"


@pytest.fixture(params=EXAMPLES, ids=lambda p: p.stem)
def example(request) -> tuple[Path, ScrapeScript]:
    path = request.param
    return path, ScrapeScript.from_yaml(path.read_text(encoding="utf-8"))


def test_it_parses_as_a_script(example):
    _, script = example
    assert script.steps
    assert script.emitted_names, "a script that emits nothing produces no rows"


def test_it_carries_real_validation_rules(example):
    """An example without them teaches the habit this project exists to break:
    treating 'nothing raised' as 'it worked'."""
    path, script = example
    v = script.validation
    assert v.min_rows > 1, f"{path.name}: min_rows of {v.min_rows} asserts nothing"
    assert v.max_null_rate, f"{path.name}: no per-field null rate"
    assert v.unique or v.required_fields, f"{path.name}: nothing pins row identity"


def test_it_declares_an_output_schema(example):
    """The schema is the contract a consumer reads. An example should show one."""
    path, script = example
    assert script.output_schema, f"{path.name} has no output_schema"
    assert script.output_schema.get("required"), f"{path.name}: no required fields"


def test_every_validated_field_is_actually_extracted(example):
    path, script = example
    extracted = set(script.field_names())
    for field in script.validation.max_null_rate:
        assert field in extracted, f"{path.name}: rule on {field!r}, which nothing extracts"
    for field in script.validation.required_fields:
        assert field in extracted, f"{path.name}: requires {field!r}, which nothing extracts"


def test_it_ships_no_unsandboxed_python(example):
    """`custom_python` runs unsandboxed and always needs human approval. An
    example is the last place to normalise it."""
    path, script = example
    assert not script.has_custom_python, f"{path.name} contains a custom_python step"


def test_it_is_polite_by_default(example):
    path, script = example
    assert script.respect_robots, f"{path.name} disables robots.txt"
    assert script.rate_limit.min_delay_s >= 0.5, f"{path.name} hammers the site"
    assert script.rate_limit.max_pages_per_run <= 50, f"{path.name} walks too far in one run"


def test_it_explains_itself(example):
    """A bare YAML file teaches the syntax and none of the reasoning."""
    path, _ = example
    head = path.read_text(encoding="utf-8").split("version:")[0]
    assert head.count("#") >= 5, f"{path.name} has no explanatory header"


def test_it_targets_a_site_that_invites_it(example):
    """The repository ships a tool, not a tool aimed at somebody. An example that
    names a commercial target is a different artifact from an example."""
    path, script = example
    urls = " ".join(getattr(s, "url", "") or "" for s in script.steps)
    assert "toscrape.com" in urls or "example." in urls, (
        f"{path.name} points at {urls!r}; examples should use a site that exists "
        f"to be scraped"
    )


# ------------------------------------------------------------------ the README
def test_the_readme_does_not_claim_maturity_it_cannot_support():
    """"Used in anger since" was in here, on a repository sixteen hours old whose
    repair loop had never fired outside a test. Words like that are cheap to
    write and expensive to retract, so they are checked rather than trusted."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    low = readme.lower()
    for phrase in (
        "used in anger",
        "battle-tested",
        "battle tested",
        "production-ready",
        "production ready",
        "rock solid",
        "enterprise-grade",
    ):
        assert phrase not in low, f"the README claims {phrase!r}"


def test_the_readme_says_what_has_never_been_exercised():
    """The repair loop is the whole pitch and it has never run on a real
    breakage. A reader deserves that in the README, not in a commit message."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    low = readme.lower()
    assert "never" in low, "nothing in the README admits a limit"
    assert "repair loop has never" in low or "never fired" in low, (
        "the README should say the repair loop is unproven on a real site change"
    )
