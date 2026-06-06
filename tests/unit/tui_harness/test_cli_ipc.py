from __future__ import annotations

import json
import socketserver
import threading
from pathlib import Path

from tools.tui_harness import cli


class _FakeHandler(socketserver.StreamRequestHandler):
    seen_request: dict[str, object] | None = None

    def handle(self) -> None:
        raw = self.rfile.readline()
        request = json.loads(raw.decode("utf-8"))
        _FakeHandler.seen_request = request
        response = {
            "ok": request.get("token") == "secret-token",
            "action": request.get("action"),
        }
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


def test_request_uses_session_json_loopback_endpoint(tmp_path: Path) -> None:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = tmp_path / "session"
    session.mkdir()
    (session / "session.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": server.server_address[1],
                "token": "secret-token",
            }
        ),
        encoding="utf-8",
    )

    try:
        response = cli._request(session, {"action": "observe"})
    finally:
        server.shutdown()
        server.server_close()

    assert response == {"ok": True, "action": "observe"}
    assert _FakeHandler.seen_request == {
        "action": "observe",
        "token": "secret-token",
    }
