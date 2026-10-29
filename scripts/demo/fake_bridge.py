"""A fake Hue bridge for the local demo: HTTPS on 127.0.0.1 with a self-signed key.

It answers only what Glowcast's setup flow and settings screen ask for:

- GET  /api/config: the bridge answers (the reachability check);
- POST /api: Hue's pairing reply, error 101 ("link button not pressed") until
  the link button is pressed, then a generic app key and client key;
- GET  /clip/v2/resource/entertainment_configuration: two entertainment areas;
- POST /demo/press-link-button: presses the link button (for the recording
  script); the next pairing request succeeds.

Usage: python fake_bridge.py <directory for the key> <port>
"""

import datetime
import http.server
import ipaddress
import json
import ssl
import sys
import threading
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HOST = "127.0.0.1"
AREAS = [
    {"id": "area-tv", "name": "TV area", "channels": 5},
    {"id": "area-room", "name": "Whole room", "channels": 3},
]
# Generic demo values, made up for this fake bridge.
APP_KEY = "demo-app-key"
CLIENT_KEY = "00112233445566778899AABBCCDDEEFF"


class Bridge:
    """The bridge's state: whether the link button is pressed."""

    def __init__(self) -> None:
        """Start with the link button not pressed."""
        self._lock = threading.Lock()  # requests come in on several threads
        self._button_pressed = False

    def press_link_button(self) -> None:
        """Let the next pairing request succeed."""
        with self._lock:
            self._button_pressed = True

    def pair(self) -> list[dict[str, Any]]:
        """Hue's reply to POST /api; a pressed button pairs one app."""
        with self._lock:
            pressed, self._button_pressed = self._button_pressed, False
        if not pressed:
            return [
                {
                    "error": {
                        "type": 101,
                        "address": "",
                        "description": "link button not pressed",
                    }
                }
            ]
        return [{"success": {"username": APP_KEY, "clientkey": CLIENT_KEY}}]


def areas() -> dict[str, Any]:
    """The entertainment configurations in CLIP v2 form."""
    return {
        "errors": [],
        "data": [
            {
                "id": area["id"],
                "type": "entertainment_configuration",
                "metadata": {"name": area["name"]},
                "status": "inactive",
                "channels": [
                    {
                        "channel_id": channel,
                        "position": {"x": channel / 2 - 1, "y": 0.8, "z": 0.0},
                        "members": [],
                    }
                    for channel in range(area["channels"])
                ],
            }
            for area in AREAS
        ],
    }


def make_handler(bridge: Bridge) -> type[http.server.BaseHTTPRequestHandler]:
    """A request handler bound to the bridge's state."""

    class Handler(http.server.BaseHTTPRequestHandler):
        # A client that stalls (also in the TLS handshake) gives up its thread.
        timeout = 10

        def do_GET(self) -> None:
            if self.path == "/api/config":
                self._reply({"name": "Demo bridge", "apiversion": "1.70.0"})
            elif self.path.startswith("/clip/v2/resource/entertainment_configuration"):
                if self.headers.get("hue-application-key") != APP_KEY:
                    self._reply({"errors": [{"description": "unauthorized"}]}, 403)
                else:
                    self._reply(areas())
            else:
                self._reply({"errors": [{"description": "not found"}]}, 404)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/api":
                self._reply(bridge.pair())
            elif self.path == "/demo/press-link-button":
                bridge.press_link_button()
                self._reply({"pressed": True})
            else:
                self._reply({"errors": [{"description": "not found"}]}, 404)

        def _reply(self, body: Any, status: int = 200) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"fake bridge: {self.command} {self.path}", flush=True)

    return Handler


def make_key(directory: Path) -> tuple[Path, Path]:
    """Write a self-signed certificate for 127.0.0.1; return (cert, key)."""
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "glowcast-demo")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(HOST))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def main() -> None:
    """Serve until killed."""
    directory, port = Path(sys.argv[1]), int(sys.argv[2])
    cert, key = make_key(directory)
    server = http.server.ThreadingHTTPServer((HOST, port), make_handler(Bridge()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    # The handshake runs in each request's own thread, not in accept(), so a
    # stalled client cannot block the others.
    server.socket = context.wrap_socket(
        server.socket, server_side=True, do_handshake_on_connect=False
    )
    print(f"fake bridge: listening on https://{HOST}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
