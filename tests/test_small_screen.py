"""Every control on every page must be reachable at every size.

Why this exists. On a phone the new-scraper form showed 5 of its 24 controls.
The page did not scroll, so there was no way to get at the rest, including the
field the form refuses to submit without. Someone filled in the URL, could not
find the goal box, submitted, and got told to say what to extract.

The cause was structural, not cosmetic: the dense console layout divides one
screen between stacked panels, each clipping its own contents against
`overflow: hidden`. It clipped at 390px and again at 1180px, where panels begin
stacking, and on a 768px-tall desktop window where a side column's last panel
collapsed to 23px and cut off its own header.

Nothing in the rest of the suite could see this. Route tests assert a 200.
Template tests assert markup is present. Present and reachable are different
things, and only a real browser at a real size can tell them apart.

Skipped when Chromium is unavailable, so it never blocks a machine without it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

from smartscraper.db.models import Scraper, ScriptVersion, VersionStatus  # noqa: E402
from smartscraper.web.app import create_app  # noqa: E402

# One narrow, one tablet, one at the stacking breakpoint, one short desktop.
SIZES = [(390, 844), (768, 1024), (1180, 800), (1366, 768), (1440, 900)]

PATHS = [
    "/", "/scrapers", "/scrapers/new", "/runs", "/records", "/repairs",
    "/network", "/coverage", "/delivery", "/mcp-console", "/settings",
    "/audit", "/styleguide", "/builder", "/palette",
]

PROBE = """
() => {
  const out = [];
  const doc = document.documentElement;
  for (const el of document.querySelectorAll('input:not([type=hidden]), textarea, select, button, a.btn')) {
    let target = el;
    if (el.type === 'radio' || el.type === 'checkbox') {
      const lab = el.closest('label') || (el.id && document.querySelector(`label[for="${el.id}"]`));
      if (lab) target = lab;
    }
    const r = target.getBoundingClientRect();
    let clipped = null;
    for (let p = target.parentElement; p && p !== doc; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflow === 'hidden' || cs.overflowY === 'hidden') {
        const pr = p.getBoundingClientRect();
        if (r.bottom > pr.bottom + 1 || r.top < pr.top - 1) {
          clipped = (p.className || p.tagName).toString().slice(0, 40);
          break;
        }
      }
    }
    const name = el.getAttribute('name') || el.getAttribute('aria-label') ||
                 (el.textContent || '').trim().slice(0, 24) || el.tagName.toLowerCase();
    if (clipped || r.width === 0 || r.height === 0) {
      out.push({name, clipped, w: Math.round(r.width), h: Math.round(r.height)});
    }
  }
  return {bad: out, sideways: doc.scrollWidth > doc.clientWidth + 1,
          scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth};
}
"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A real server on a real port. TestClient cannot lay anything out.

    The schema and the seed row are written synchronously, before uvicorn owns
    an event loop in this process; an async seed afterwards collides with it.
    """
    import socket
    import threading
    import time

    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import Session

    from smartscraper.db.models import Base

    tmp = tmp_path_factory.mktemp("smallscreen")
    db = tmp / "ss.db"

    sync = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(sync)
    with Session(sync) as sess:
        sc = Scraper(name="sized", url="https://example.test/", yaml_path="sized.yaml")
        sess.add(sc)
        sess.flush()
        sess.add(ScriptVersion(scraper_id=sc.id, version=1, yaml="version: 1\nsteps: []\n",
                               status=VersionStatus.ACTIVE))
        sess.commit()
        scraper_id = sc.id
    sync.dispose()

    app = create_app(create_async_engine(f"sqlite+aiosqlite:///{db}", future=True), mount_mcp=False)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not srv.started and time.time() < deadline:
        time.sleep(0.1)
    if not srv.started:
        pytest.skip("the test server did not start")

    try:
        yield f"http://127.0.0.1:{port}", scraper_id
    finally:
        srv.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium is not available: {exc}")
            yield b
            b.close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"playwright could not start: {exc}")


@pytest.mark.parametrize("width,height", SIZES)
def test_every_control_is_reachable(browser, server, width, height):
    base, scraper_id = server
    paths = [*PATHS, f"/scrapers/{scraper_id}"]
    page = browser.new_page(viewport={"width": width, "height": height})
    problems: list[str] = []
    try:
        for path in paths:
            page.goto(f"{base}{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(120)
            result = page.evaluate(PROBE)
            for bad in result["bad"]:
                where = bad["clipped"] or f"{bad['w']}x{bad['h']}"
                problems.append(f"{path} @{width}x{height}: {bad['name']!r} unreachable ({where})")
            if result["sideways"]:
                problems.append(
                    f"{path} @{width}x{height}: scrolls sideways "
                    f"({result['scrollWidth']} > {result['clientWidth']})"
                )
    finally:
        page.close()

    assert not problems, "\n".join(problems)
