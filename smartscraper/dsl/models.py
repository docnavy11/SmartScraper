"""The scrape script DSL.

This is the contract between the agents that write scripts, the runner that
executes them, and the validator that judges the output. Agents emit this shape
and nothing else. Free-form Python is confined to the `custom_python` step,
which always requires human approval to promote.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import Field as PField

SCHEMA_VERSION = 1


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- enums
class Engine(StrEnum):
    AUTO = "auto"
    HTTP = "http"
    PATCHRIGHT = "patchright"
    PLAYWRIGHT = "playwright"
    CAMOUFOX = "camoufox"


class Rung(StrEnum):
    """One step of the escalation ladder. Ordered cheapest to most expensive."""

    HTTP = "http"
    PATCHRIGHT = "patchright"
    PATCHRIGHT_PROXY = "patchright+proxy"
    CAMOUFOX = "camoufox"
    BYPARR = "byparr"


class PromotionPolicy(StrEnum):
    MANUAL = "manual"
    AUTO_IF_MINOR = "auto_if_minor"
    AUTO = "auto"


class Parse(StrEnum):
    TEXT = "text"
    MONEY = "money"
    INT = "int"
    FLOAT = "float"
    DATE = "date"
    DATETIME = "datetime"
    BOOL = "bool"
    JSON = "json"


class Rotation(StrEnum):
    ROUND_ROBIN = "round_robin"
    STICKY_PER_RUN = "sticky_per_run"


# --------------------------------------------------------------------------- fields
class Field(Strict):
    """One extracted value inside a record."""

    selector: str
    attr: str = "text"
    parse: Parse = Parse.TEXT
    absolute: bool = False
    required: bool = False
    regex: str | None = None
    default: Any = None
    fallback_selectors: list[str] = PField(default_factory=list)

    @property
    def candidates(self) -> list[str]:
        return [self.selector, *self.fallback_selectors]


# --------------------------------------------------------------------------- steps
class _Step(Strict):
    note: str | None = None


class Goto(_Step):
    op: Literal["goto"]
    url: str
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "domcontentloaded"
    timeout_ms: int = 30_000


class WaitFor(_Step):
    op: Literal["wait_for"]
    selector: str | None = None
    state: Literal["attached", "detached", "visible", "hidden"] = "visible"
    timeout_ms: int = 15_000
    url_contains: str | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if not self.selector and not self.url_contains:
            raise ValueError("wait_for needs a selector or url_contains")
        return self


class Click(_Step):
    op: Literal["click"]
    selector: str
    optional: bool = False
    timeout_ms: int = 10_000


class Fill(_Step):
    op: Literal["fill"]
    selector: str
    value: str
    secret: str | None = PField(default=None, description="name in the secret store; overrides value")


class Select(_Step):
    op: Literal["select"]
    selector: str
    value: str


class Scroll(_Step):
    op: Literal["scroll"]
    to: Literal["bottom", "top", "selector"] = "bottom"
    selector: str | None = None
    times: int = 1
    pause_ms: int = 700


class Hover(_Step):
    op: Literal["hover"]
    selector: str


class Press(_Step):
    op: Literal["press"]
    key: str
    selector: str | None = None


class Extract(_Step):
    """Extract a single record from the page."""

    op: Literal["extract"]
    as_: str = PField(alias="as")
    fields: dict[str, Field]
    selector: str | None = None


class ExtractList(_Step):
    """Extract one record per element matching `selector`."""

    op: Literal["extract_list"]
    selector: str
    as_: str = PField(alias="as")
    fields: dict[str, Field]
    min_items: int = 0
    fallback_selectors: list[str] = PField(default_factory=list)


class Paginate(_Step):
    op: Literal["paginate"]
    next: str | None = None
    load_more: str | None = None
    max_pages: int = 20
    var: str = "page"
    stop_when_empty: bool = True

    @model_validator(mode="after")
    def _one_of(self):
        if not self.next and not self.load_more:
            raise ValueError("paginate needs next or load_more")
        return self


class Loop(_Step):
    op: Literal["loop"]
    over: str
    as_: str = PField(alias="as")
    steps: list[Step]
    max_iterations: int = 100


class Emit(_Step):
    op: Literal["emit"]
    from_: str = PField(alias="from")


class Sleep(_Step):
    op: Literal["sleep"]
    ms: int = 1_000


class Screenshot(_Step):
    op: Literal["screenshot"]
    name: str = "step"
    full_page: bool = False


class SolveChallenge(_Step):
    op: Literal["solve_challenge"]
    kind: Literal["auto", "cloudflare", "turnstile", "recaptcha_v2", "hcaptcha"] = "auto"
    timeout_s: int = 90
    max_spend: float = 0.05


class Assert(_Step):
    op: Literal["assert"]
    selector: str | None = None
    text_contains: str | None = None
    min_count: int | None = None
    message: str = "assertion failed"


class CustomPython(_Step):
    """Escape hatch. Runs UNSANDBOXED inside the run subprocess.

    Any version that adds or changes one of these needs human approval regardless
    of the scraper's promotion policy. See `ScrapeScript.has_custom_python`.
    """

    op: Literal["custom_python"]
    code: str = PField(description="body of `def run(page, ctx): ...`")
    as_: str | None = PField(default=None, alias="as")
    timeout_s: int = 60


Step = Annotated[
    Goto
    | WaitFor
    | Click
    | Fill
    | Select
    | Scroll
    | Hover
    | Press
    | Extract
    | ExtractList
    | Paginate
    | Loop
    | Emit
    | Sleep
    | Screenshot
    | SolveChallenge
    | Assert
    | CustomPython,
    PField(discriminator="op"),
]

Loop.model_rebuild()

OPS: tuple[str, ...] = (
    "goto", "wait_for", "click", "fill", "select", "scroll", "hover", "press",
    "extract", "extract_list", "paginate", "loop", "emit", "sleep", "screenshot",
    "solve_challenge", "assert", "custom_python",
)


# --------------------------------------------------------------------------- validation
class RowCountBand(Strict):
    relative_to: Literal["last_5_runs", "last_10_runs", "last_run"] = "last_5_runs"
    tolerance: float = PField(default=0.5, ge=0, le=10)


class Validation(Strict):
    """What makes a run a pass. A run that raises no exception but fails these is
    a failure: silent wrong data is the failure mode this system exists to catch."""

    min_rows: int = 1
    max_rows: int | None = None
    row_count_band: RowCountBand | None = None
    max_null_rate: dict[str, float] = PField(default_factory=dict)
    unique: list[str] = PField(default_factory=list)
    required_fields: list[str] = PField(default_factory=list)

    @field_validator("max_null_rate")
    @classmethod
    def _rates(cls, v: dict[str, float]) -> dict[str, float]:
        for k, r in v.items():
            if not 0.0 <= r <= 1.0:
                raise ValueError(f"max_null_rate[{k}] must be between 0 and 1")
        return v


class RateLimit(Strict):
    min_delay_s: float = 2.0
    max_delay_s: float = 6.0
    max_pages_per_run: int = 20


# --------------------------------------------------------------------------- script
class ScrapeScript(Strict):
    """A complete, versioned, executable scrape definition."""

    version: int = 1
    schema_version: int = SCHEMA_VERSION
    engine: Engine = Engine.AUTO
    profile: str | None = None
    proxy: str | None = None
    rotation: Rotation = Rotation.STICKY_PER_RUN
    escalation: list[Rung] = PField(
        default_factory=lambda: [Rung.HTTP, Rung.PATCHRIGHT, Rung.PATCHRIGHT_PROXY]
    )
    rate_limit: RateLimit = PField(default_factory=RateLimit)
    respect_robots: bool = True
    steps: list[Step]
    output_schema: dict[str, Any] | None = None
    validation: Validation = PField(default_factory=Validation)

    @field_validator("steps")
    @classmethod
    def _needs_emit(cls, v: list[Step]) -> list[Step]:
        if not v:
            raise ValueError("a script needs at least one step")
        if not any(getattr(s, "op", None) == "emit" for s in _walk(v)):
            raise ValueError("a script must emit something; add an `emit` step")
        return v

    @property
    def has_custom_python(self) -> bool:
        return any(getattr(s, "op", None) == "custom_python" for s in _walk(self.steps))

    @property
    def emitted_names(self) -> list[str]:
        return [s.from_ for s in _walk(self.steps) if getattr(s, "op", None) == "emit"]

    def field_names(self) -> list[str]:
        out: list[str] = []
        for s in _walk(self.steps):
            if getattr(s, "op", None) in ("extract", "extract_list"):
                out.extend(k for k in s.fields if k not in out)
        return out

    # -- serialisation ------------------------------------------------------
    def to_yaml(self) -> str:
        import yaml

        return yaml.safe_dump(
            self.model_dump(mode="json", by_alias=True, exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        )

    @classmethod
    def from_yaml(cls, text: str) -> ScrapeScript:
        import yaml

        return cls.model_validate(yaml.safe_load(text))


def _walk(steps: list[Any]):
    for s in steps:
        yield s
        if getattr(s, "op", None) == "loop":
            yield from _walk(s.steps)


def json_schema() -> dict[str, Any]:
    """JSON Schema for the DSL, handed to the agents as the output contract."""
    return ScrapeScript.model_json_schema()
