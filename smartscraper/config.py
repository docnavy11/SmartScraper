"""Runtime settings. Every module reads configuration from here, never from os.environ."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SS_", env_file=".env", extra="ignore")

    # --- paths ---
    data_dir: Path = ROOT / "data"
    scrapers_dir: Path = ROOT / "scrapers"
    db_path: Path = ROOT / "data" / "smartscraper.db"

    # --- web ---
    host: str = "127.0.0.1"
    port: int = 8088
    web_user: str = "admin"
    web_password: str | None = Field(
        default=None,
        description="HTTP Basic password for the web UI. Required to bind anywhere "
                    "but loopback: the UI can run scrapers and approve scripts that "
                    "contain unsandboxed Python, so it is an admin panel.",
    )
    allow_insecure_bind: bool = Field(
        default=False,
        description="Bind a passwordless UI to a non-loopback address anyway. "
                    "Only sensible behind a trusted reverse proxy that authenticates.",
    )

    # --- models (decision: Agent SDK harness, opus for agents, sonnet for fallback) ---
    builder_model: str = "claude-opus-5"
    repair_model: str = "claude-opus-5"
    fallback_model: str = "claude-sonnet-5"
    agent_effort: str = "high"
    # 40 was not enough for a real category page: on a-retail-site.example the agent found
    # the listing, pinned the card container and the name link, then ran out mid-way
    # through the price selector. It was making progress, not looping.
    max_builder_turns: int = 80
    snapshot_budget_bytes: int = 40_000

    # --- budgets (USD) ---
    monthly_budget: float = 60.0
    default_scraper_budget: float = 10.0
    stop_at_budget: bool = True

    # --- engines ---
    default_engine: str = "auto"
    headed: bool = True
    max_concurrent_runs: int = 3
    run_timeout_s: int = 600
    runner_cmd: str | None = Field(
        default=None,
        description='JSON list replacing the runner subprocess argv wholesale, e.g. '
                    '\'["python","-m","smartscraper.runner.cli"]\'. Tests use it to '
                    'substitute a stub. Reads SS_RUNNER_CMD.',
    )
    respect_robots: bool = True
    min_delay_s: float = 2.0
    max_delay_s: float = 6.0

    # --- solvers ---
    byparr_url: str | None = None
    captcha_vendor: str = "capmonster"
    captcha_key: str | None = None
    auto_solve_turnstile: bool = True
    max_captcha_spend_per_run: float = 0.05

    # --- retention ---
    keep_artifacts_days: int = 30
    keep_traces_days: int = 7
    keep_audit_days: int = 365

    # --- secrets ---
    secret_key: str | None = Field(default=None, description="Fernet key for encrypted columns")
    anthropic_api_key: str | None = None
    mcp_bearer_token: str | None = None

    @property
    def headed_effective(self) -> bool:
        """`headed` is a preference; a display is a precondition.

        Headed browsers are less detectable, so it defaults on. On a headless
        host Chromium exits with "Missing X server or $DISPLAY" and the whole
        build fails for a reason that has nothing to do with the page. Prefer a
        working headless run over a headed one that cannot start.
        """
        import os

        if self.headed and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
        return self.headed

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.runs_dir, self.fixtures_dir, self.profiles_dir, self.scrapers_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
