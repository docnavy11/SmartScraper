"""Register a YAML script as a scraper.

The builder writes a scraper and registers it in one motion, which left no way
to bring in a script you already have: an example from `examples/`, one copied
between machines, or one written by hand. The README told people to copy a file
into `scrapers/` and run it, and that quietly did nothing, because a run is
looked up in the database and a loose file is not in it.

Validation happens before anything is written. A file that does not parse as a
`ScrapeScript` is refused with the pydantic error, not stored and then failing
at the point of use.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from smartscraper import repo
from smartscraper.config import get_settings
from smartscraper.db.models import Author, Scraper, ScriptVersion, VersionStatus
from smartscraper.dsl.models import ScrapeScript

log = logging.getLogger(__name__)


class ImportRefused(ValueError):
    """The file is not a usable script, or the name is already taken."""


@dataclass(slots=True)
class Imported:
    scraper_id: int
    name: str
    path: Path
    version: int
    has_custom_python: bool


def slugify(text: str) -> str:
    """Same shape the builder uses, so a name means one thing in both paths."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or "scraper"


def first_url(script: ScrapeScript) -> str:
    for step in script.steps:
        url = getattr(step, "url", None)
        if url:
            return str(url)
    return ""


def name_from_url(url: str) -> str:
    """A short readable name from a URL, for when nothing better was given.

    Slugifying the whole URL produced
    `https-books-toscrape-com-catalogue-category-books-mystery-3-index-html`,
    which is a name nobody would type. Host plus the most specific path segment
    that is not a filename gets close to what a person would have called it.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.").split(".")[0]
    segments = [
        seg for seg in parsed.path.split("/")
        if seg and "." not in seg and seg not in ("index", "catalogue", "category")
    ]
    tail = segments[-1] if segments else ""
    return slugify(f"{host}-{tail}" if tail else host or "scraper")


async def import_script(
    session: AsyncSession,
    text: str,
    *,
    name: str | None = None,
    goal: str = "",
    actor: str = "you",
    activate: bool = True,
) -> Imported:
    """Validate a YAML script, write it into `scrapers/`, and register it."""
    try:
        script = ScrapeScript.from_yaml(text)
    except Exception as exc:
        raise ImportRefused(f"this is not a valid scrape script: {exc}") from exc

    slug = slugify(name) if name else name_from_url(first_url(script))
    if await repo.get_scraper(session, slug) is not None:
        raise ImportRefused(
            f"a scraper named {slug!r} already exists; pass a different name"
        )

    settings = get_settings()
    settings.ensure_dirs()
    path = settings.scrapers_dir / f"{slug}.yaml"
    path.write_text(text, encoding="utf-8")

    scraper = Scraper(
        name=slug,
        url=first_url(script),
        goal=goal,
        yaml_path=path.name,
        promotion_policy="manual",
        budget_usd_month=settings.default_scraper_budget,
        respect_robots=script.respect_robots,
    )
    session.add(scraper)
    await session.flush()

    session.add(
        ScriptVersion(
            scraper_id=scraper.id,
            version=script.version,
            yaml=text,
            created_by=Author.HUMAN,
            change_summary="imported",
            status=VersionStatus.ACTIVE if activate else VersionStatus.CANDIDATE,
            has_custom_python=script.has_custom_python,
            output_schema=script.output_schema,
        )
    )
    await repo.log(
        session, actor=actor, action="imported scraper", object_type="scraper",
        object_ref=slug,
        detail=f"v{script.version}, {len(script.steps)} steps, "
               f"{'contains custom_python' if script.has_custom_python else 'no custom_python'}",
    )
    await session.flush()

    if script.has_custom_python:
        # Say it out loud. An imported file is code somebody else wrote, and this
        # step runs unsandboxed in the run subprocess.
        log.warning(
            "imported %s contains a custom_python step, which runs unsandboxed", slug
        )

    return Imported(
        scraper_id=scraper.id,
        name=slug,
        path=path,
        version=script.version,
        has_custom_python=script.has_custom_python,
    )
