"""SQLAlchemy 2 models. Mirrors PLAN.md section 6.

Note on truth: `scrapers/*.yaml` on disk is the source of truth for a script.
Rows here mirror it so the UI can query, and carry everything a file cannot:
runs, metrics, records, deliveries, spend and the audit trail.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    VALIDATION_FAILED = "validation_failed"
    ERROR = "error"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class VersionStatus(enum.StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    RETIRED = "retired"


class Author(enum.StrEnum):
    BUILDER = "builder"
    REPAIR = "repair"
    HUMAN = "human"


class RecordSource(enum.StrEnum):
    SCRIPT = "script"
    LLM_FALLBACK = "llm_fallback"


class TS:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- scraper
class Scraper(Base, TS):
    __tablename__ = "scraper"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text, default="")
    yaml_path: Mapped[str] = mapped_column(Text)
    schedule: Mapped[str | None] = mapped_column(String(80), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    promotion_policy: Mapped[str] = mapped_column(String(20), default="manual")
    fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_model: Mapped[str | None] = mapped_column(String(60), default=None)
    budget_usd_month: Mapped[float] = mapped_column(Float, default=10.0)
    proxy_pool: Mapped[str | None] = mapped_column(String(80), default=None)
    profile: Mapped[str | None] = mapped_column(String(80), default=None)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    acknowledged_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ack_note: Mapped[str | None] = mapped_column(Text, default=None)

    versions: Mapped[list[ScriptVersion]] = relationship(
        back_populates="scraper", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="scraper", cascade="all, delete-orphan")


class ScriptVersion(Base, TS):
    __tablename__ = "script_version"
    __table_args__ = (UniqueConstraint("scraper_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    scraper_id: Mapped[int] = mapped_column(ForeignKey("scraper.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    yaml: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(20), default="human")
    change_summary: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    has_custom_python: Mapped[bool] = mapped_column(Boolean, default=False)
    output_schema: Mapped[dict | None] = mapped_column(JSON, default=None)
    test_run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), default=None)
    approved_by: Mapped[str | None] = mapped_column(String(40), default=None)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    scraper: Mapped[Scraper] = relationship(back_populates="versions")


# --------------------------------------------------------------------------- runs
class Run(Base, TS):
    __tablename__ = "run"
    id: Mapped[int] = mapped_column(primary_key=True)
    scraper_id: Mapped[int] = mapped_column(ForeignKey("scraper.id", ondelete="CASCADE"), index=True)
    script_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="schedule")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    engine_used: Mapped[str | None] = mapped_column(String(40), default=None)
    escalation_level: Mapped[int] = mapped_column(Integer, default=1)
    proxy_used: Mapped[str | None] = mapped_column(String(120), default=None)
    block_reason: Mapped[str | None] = mapped_column(String(60), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    validator_report: Mapped[dict | None] = mapped_column(JSON, default=None)
    log_path: Mapped[str | None] = mapped_column(Text, default=None)
    artifact_dir: Mapped[str | None] = mapped_column(Text, default=None)

    scraper: Mapped[Scraper] = relationship(back_populates="runs")


Index("ix_run_scraper_created", Run.scraper_id, Run.created_at.desc())


class RunMetric(Base):
    __tablename__ = "run_metric"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(120))
    null_rate: Mapped[float] = mapped_column(Float, default=0.0)
    distinct_count: Mapped[int] = mapped_column(Integer, default=0)
    sample: Mapped[str | None] = mapped_column(Text, default=None)


class Record(Base, TS):
    __tablename__ = "record"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    scraper_id: Mapped[int] = mapped_column(ForeignKey("scraper.id", ondelete="CASCADE"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16), default="script", index=True)
    row_hash: Mapped[str] = mapped_column(String(64), index=True)


# --------------------------------------------------------------------------- delivery
class DeliveryTarget(Base, TS):
    __tablename__ = "delivery_target"
    id: Mapped[int] = mapped_column(primary_key=True)
    scraper_id: Mapped[int | None] = mapped_column(ForeignKey("scraper.id", ondelete="CASCADE"), default=None)
    kind: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    fmt: Mapped[str] = mapped_column(String(24), default="json")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    only_on_failure: Mapped[bool] = mapped_column(Boolean, default=False)


class Delivery(Base, TS):
    __tablename__ = "delivery"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("delivery_target.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    rows_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


# --------------------------------------------------------------------------- repair & spend
class Repair(Base, TS):
    __tablename__ = "repair"
    id: Mapped[int] = mapped_column(primary_key=True)
    scraper_id: Mapped[int] = mapped_column(ForeignKey("scraper.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), default=None)
    candidate_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending_approval", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    test_run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), default=None)
    transcript_path: Mapped[str | None] = mapped_column(Text, default=None)
    decided_by: Mapped[str | None] = mapped_column(String(40), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class LlmUsage(Base, TS):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    scraper_id: Mapped[int | None] = mapped_column(
        ForeignKey("scraper.id", ondelete="SET NULL"), default=None
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"), default=None)
    agent: Mapped[str] = mapped_column(String(24), index=True)
    model: Mapped[str] = mapped_column(String(60))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    turns: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- infra
class Profile(Base, TS):
    __tablename__ = "profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    domain: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(30), default="storage_state")
    storage_state_path: Mapped[str | None] = mapped_column(Text, default=None)
    user_data_dir: Mapped[str | None] = mapped_column(Text, default=None)
    totp_secret_ref: Mapped[str | None] = mapped_column(String(80), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    notes: Mapped[str] = mapped_column(Text, default="")


class ProxyPool(Base, TS):
    __tablename__ = "proxy_pool"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    vendor: Mapped[str] = mapped_column(String(60), default="")
    kind: Mapped[str] = mapped_column(String(30), default="residential")
    entries: Mapped[list] = mapped_column(JSON, default=list)
    rotation: Mapped[str] = mapped_column(String(24), default="sticky_per_run")
    sticky_ttl_s: Mapped[int] = mapped_column(Integer, default=600)
    username_template: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    gb_used: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Secret(Base, TS):
    __tablename__ = "secret"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    value_enc: Mapped[bytes] = mapped_column()
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AuditEntry(Base, TS):
    """Append only. Never updated, never deleted before retention."""

    __tablename__ = "audit_entry"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_ref: Mapped[str] = mapped_column(String(160), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
