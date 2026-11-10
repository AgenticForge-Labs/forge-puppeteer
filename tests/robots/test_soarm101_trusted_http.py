"""Security regressions for the private SDK choreography transport."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from forge_puppeteer.devices.soarm101_trusted_http import (
    SOARM101TrustedChoreographyHTTP,
)

PIN = "sha256:" + "a" * 64


@pytest.mark.parametrize("url", [
    "http://example.com:8765", "http://127.0.0.1", "https://127.0.0.1:8765",
    "http://user:secret@127.0.0.1:8765", "http://127.0.0.1:8765/path",
    "http://127.0.0.1:8765/?token=x", "http://127.0.0.1:8765/#fragment",
])
def test_trusted_transport_rejects_untrusted_or_ambiguous_origins(url):
    with pytest.raises(ValueError, match="loopback"):
        SOARM101TrustedChoreographyHTTP(url, "sensitive-token", {"idle": PIN})


def test_trusted_transport_does_not_follow_redirect_or_leak_token():
    seen = []

    class Destination(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    thread = threading.Thread(target=destination.serve_forever, daemon=True)
    thread.start()

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/steal")
            self.end_headers()

        def log_message(self, *args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    redirect_thread = threading.Thread(target=source.serve_forever, daemon=True)
    redirect_thread.start()
    try:
        token = "must-never-leak"
        client = SOARM101TrustedChoreographyHTTP(
            f"http://127.0.0.1:{source.server_port}", token, {"idle": PIN})
        with pytest.raises(RuntimeError) as error:
            client._post(client.url, {"request_id": "test", "name": "idle", "revision": PIN})
        assert token not in str(error.value)
        assert seen == []
    finally:
        source.shutdown()
        destination.shutdown()
        source.server_close()
        destination.server_close()
        redirect_thread.join(timeout=2)
        thread.join(timeout=2)


def test_trusted_transport_ignores_environment_proxy(monkeypatch):
    import urllib.request

    client = SOARM101TrustedChoreographyHTTP("http://127.0.0.1:8765", "secret", {"idle": PIN})
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"ok": True, "result": {}}).encode()

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    assert client._post(client.url, {"request_id": "test"})["ok"] is True
    proxy = next(item for item in captured["handlers"] if isinstance(item, urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    assert type(captured["handlers"][-1]).__name__ == "_NoRedirects"
