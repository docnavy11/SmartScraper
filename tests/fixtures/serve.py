"""A real HTTP server over `tests/fixtures/`, on a random port.

The runner tests are end-to-end on purpose: a fake engine can be made to agree
with whatever `steps.py` happens to do, while a socket, a status line and real
HTML cannot. Everything except the browser rungs is exercised for real against
this server.

Two path prefixes shape the response, so a test can ask for a refusal without
needing a second server:

    /blocked/<name>.html    served with HTTP 403 and Cloudflare-ish headers
    /slow/<name>.html       served after a short delay
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent

BLOCKED_HEADERS = {
    "server": "cloudflare",
    "cf-ray": "8f2b1c0d1e2f3a4b-AMS",
    "cf-mitigated": "challenge",
}


class FixtureHandler(SimpleHTTPRequestHandler):
    """Serves the fixture directory, with `/blocked/` and `/slow/` prefixes."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # keep test output clean
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if path.startswith("/slow/"):
            time.sleep(0.25)
            self.path = path[len("/slow") :]
            return super().do_GET()
        if path.startswith("/blocked/"):
            return self._serve_blocked(path[len("/blocked/") :])
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        """Treat a form POST as a GET of its action.

        The login fixture posts to a product page. A static file server has no
        form handling, and building one would test the fixture rather than the
        runner, so the body is read and discarded and the action page served.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        return self.do_GET()

    def _serve_blocked(self, name: str) -> None:
        target = FIXTURES / Path(name).name
        body = target.read_bytes() if target.exists() else b"<html><body>Forbidden</body></html>"
        self.send_response(403)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in BLOCKED_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def fixture_server(directory: Path | None = None) -> Iterator[str]:
    """Run the server for the duration of the block; yields its base URL."""
    handler = partial(FixtureHandler, directory=str(directory or FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
