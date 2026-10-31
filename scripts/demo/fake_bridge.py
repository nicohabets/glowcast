"""A fake Hue bridge for the local demo: HTTPS on 127.0.0.1 with a self-signed key.

It answers what Glowcast's setup flow, settings screen and Hue output ask for:

- GET  /api/config: the bridge answers (the reachability check);
- POST /api: Hue's pairing reply, error 101 ("link button not pressed") until
  the link button is pressed, then a generic app key and client key: the
  first pairing gets the first of KEYS, the next one the next (in turn), so
  two Glowcast setups have their own key and application id (#299);
- GET  /auth/v1: the key's application id, in the `hue-application-id` header;
- GET  /clip/v2/resource/entertainment_configuration[/<id>]: two
  entertainment areas (or one), with their streaming status;
- PUT  /clip/v2/resource/entertainment_configuration/<id>: `action` `start`
  or `stop` claims or releases the area; the area's `active_streamer` is the
  application id of the key that started it, and a start while another area
  streams is refused (503), as one stream per bridge;
- POST /demo/press-link-button: presses the link button (for the recording
  script); the next pairing request succeeds.

With --dtls-port it also runs the entertainment stream's side (for the
end-to-end run, scripts/demo/e2e.py): a minimal DTLS 1.2 server on UDP
127.0.0.1, just enough for hue-entertainment's client (PSK with the app key as
identity and the client key as key, TLS_PSK_WITH_AES_128_GCM_SHA256), which
decrypts each HueStream message. A record only decrypts with the right key.

Every request and every colour message goes as one JSON line to
<directory>/events.jsonl, so a test can check what the bridge received.

Usage: python fake_bridge.py <directory for the key and events> <port>
                             [--dtls-port PORT]
"""

import argparse
import datetime
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import socket
import ssl
import struct
import threading
import time
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

HOST = "127.0.0.1"
# Real bridges use UUIDs as ids, and hue-entertainment needs them.
TV_AREA_ID = "aaaaaaaa-0000-4000-8000-000000000001"
ROOM_AREA_ID = "aaaaaaaa-0000-4000-8000-000000000002"
AREAS = [
    {"id": TV_AREA_ID, "name": "TV area", "channels": 5},
    {"id": ROOM_AREA_ID, "name": "Whole room", "channels": 3},
]
# Generic demo values, made up for this fake bridge.
APP_KEY = "demo-app-key"
CLIENT_KEY = "00112233445566778899AABBCCDDEEFF"
APP_ID = "aaaaaaaa-0000-4000-8000-0000000000ff"
# Every app the bridge pairs, in turn: (app key, client key, application id).
KEYS = [
    (APP_KEY, CLIENT_KEY, APP_ID),
    (
        "demo-app-key-2",
        "FFEEDDCCBBAA99887766554433221100",
        "aaaaaaaa-0000-4000-8000-0000000000fe",
    ),
]
APP_IDS = {key: app_id for key, _, app_id in KEYS}
CLIENT_KEYS = {key: bytes.fromhex(client_key) for key, client_key, _ in KEYS}


def channel_position(channel: int) -> dict[str, float]:
    """Spread from left (x = -1) to right (x = 1); channel 2 at the top centre.

    The top one samples the top rows of the picture, so a letterbox picture
    shows whether its black bars are left out.
    """
    return {"x": channel / 2 - 1, "y": 0.8, "z": 1.0 if channel == 2 else 0.0}


class Events:
    """One JSON line per event, appended to a file; safe from several threads."""

    def __init__(self, path: Path) -> None:
        """Start a new file."""
        self._lock = threading.Lock()
        self._file = open(path, "w", encoding="utf-8")  # noqa: SIM115 - open for the run

    def add(self, event: str, **fields: Any) -> None:
        """Write the event with the time (seconds since the epoch)."""
        line = json.dumps({"time": time.time(), "event": event, **fields})
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()


class Bridge:
    """The bridge's state: the link button, and which area streams."""

    def __init__(self, events: Events) -> None:
        """Start with the link button not pressed and no area active."""
        self._lock = threading.Lock()  # requests come in on several threads
        self._button_pressed = False
        self._paired = 0
        self.active_area: str | None = None
        self.active_streamer: str | None = None  # its application id
        self.events = events

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
        with self._lock:
            app_key, client_key, _ = KEYS[self._paired % len(KEYS)]
            self._paired += 1
        return [{"success": {"username": app_key, "clientkey": client_key}}]

    def set_action(self, area_id: str, action: str, app_key: str) -> bool:
        """Start or stop streaming to an area; False when another app's
        stream holds the bridge (one stream per bridge)."""
        with self._lock:
            if action == "start":
                if self.active_area is not None and (
                    self.active_streamer != APP_IDS[app_key]
                ):
                    return False
                self.active_area, self.active_streamer = area_id, APP_IDS[app_key]
            elif action == "stop" and self.active_area == area_id:
                self.active_area = self.active_streamer = None
        return True

    def areas(self, area_id: str | None = None) -> dict[str, Any]:
        """The entertainment configurations in CLIP v2 form (or just one)."""
        with self._lock:
            active, streamer = self.active_area, self.active_streamer
        return {
            "errors": [],
            "data": [
                {
                    "id": area["id"],
                    "type": "entertainment_configuration",
                    "metadata": {"name": area["name"]},
                    "status": "active" if area["id"] == active else "inactive",
                    **(
                        {"active_streamer": {"rid": streamer, "rtype": "auth_v1"}}
                        if area["id"] == active
                        else {}
                    ),
                    "channels": [
                        {
                            "channel_id": channel,
                            "position": channel_position(channel),
                            "members": [],
                        }
                        for channel in range(area["channels"])
                    ],
                }
                for area in AREAS
                if area_id is None or area["id"] == area_id
            ],
        }


AREAS_PATH = "/clip/v2/resource/entertainment_configuration"


def make_handler(bridge: Bridge) -> type[http.server.BaseHTTPRequestHandler]:
    """A request handler bound to the bridge's state."""

    class Handler(http.server.BaseHTTPRequestHandler):
        # A client that stalls (also in the TLS handshake) gives up its thread.
        timeout = 10

        def do_GET(self) -> None:
            if self.path == "/api/config":
                self._reply({"name": "Demo bridge", "apiversion": "1.70.0"})
            elif not self._authorized():
                return
            elif self.path == "/auth/v1":
                key = self.headers.get("hue-application-key", "")
                self._reply({}, headers={"hue-application-id": APP_IDS[key]})
            elif self.path == AREAS_PATH:
                self._reply(bridge.areas())
            elif self.path.startswith(f"{AREAS_PATH}/"):
                area = bridge.areas(self.path.removeprefix(f"{AREAS_PATH}/"))
                if area["data"]:
                    self._reply(area)
                else:
                    self._reply({"errors": [{"description": "not found"}]}, 404)
            else:
                self._reply({"errors": [{"description": "not found"}]}, 404)

        def do_POST(self) -> None:
            self._body()
            if self.path == "/api":
                self._reply(bridge.pair())
            elif self.path == "/demo/press-link-button":
                bridge.press_link_button()
                self._reply({"pressed": True})
            else:
                self._reply({"errors": [{"description": "not found"}]}, 404)

        def do_PUT(self) -> None:
            body = self._body()
            if not self._authorized():
                return
            area_id = self.path.removeprefix(f"{AREAS_PATH}/")
            if not self.path.startswith(f"{AREAS_PATH}/") or not any(
                area["id"] == area_id for area in AREAS
            ):
                self._reply({"errors": [{"description": "not found"}]}, 404)
                return
            action = body.get("action") if isinstance(body, dict) else None
            key = self.headers.get("hue-application-key", "")
            # Which app, by its place in KEYS: never the key itself.
            app = next(n for n, (k, _, _) in enumerate(KEYS) if k == key)
            if not bridge.set_action(area_id, str(action), key):
                bridge.events.add(
                    "action", area=area_id, action=action, app=app, refused=True
                )
                self._reply({"errors": [{"description": "stream in use"}]}, 503)
                return
            bridge.events.add("action", area=area_id, action=action, app=app)
            self._reply(
                {
                    "errors": [],
                    "data": [{"rid": area_id, "rtype": "entertainment_configuration"}],
                }
            )

        def _authorized(self) -> bool:
            if self.headers.get("hue-application-key") in APP_IDS:
                return True
            self._reply({"errors": [{"description": "unauthorized"}]}, 403)
            return False

        def _body(self) -> Any:
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                return json.loads(data) if data else None
            except ValueError:
                return None

        def _reply(
            self, body: Any, status: int = 200, headers: dict[str, str] | None = None
        ) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            bridge.events.add("request", method=self.command, path=self.path)
            print(f"fake bridge: {self.command} {self.path}", flush=True)

    return Handler


# --- The entertainment stream: a minimal DTLS 1.2 PSK server --------------
# Just what hue-entertainment 0.1.2's client (hue_entertainment/dtls.py) does:
# one cipher suite, the full handshake with a cookie, no fragmentation, no
# retransmission of the server's flights (the client resends its hello).

_CT_CHANGE_CIPHER_SPEC = 0x14
_CT_HANDSHAKE = 0x16
_CT_APPLICATION_DATA = 0x17
_HT_CLIENT_HELLO = 0x01
_HT_SERVER_HELLO = 0x02
_HT_HELLO_VERIFY_REQUEST = 0x03
_HT_SERVER_HELLO_DONE = 0x0E
_HT_CLIENT_KEY_EXCHANGE = 0x10
_HT_FINISHED = 0x14
_VERSION = 0xFEFD  # DTLS 1.2
_CIPHER_SUITE = 0x00A8  # TLS_PSK_WITH_AES_128_GCM_SHA256
HUESTREAM_HEADER = 16  # "HueStream", version, sequence, reserved, colour space
AREA_ID_LENGTH = 36


def prf(secret: bytes, label: bytes, seed: bytes, length: int) -> bytes:
    """The TLS 1.2 PRF with SHA-256."""
    result = b""
    a = hmac.new(secret, label + seed, hashlib.sha256).digest()
    while len(result) < length:
        result += hmac.new(secret, a + label + seed, hashlib.sha256).digest()
        a = hmac.new(secret, a, hashlib.sha256).digest()
    return result[:length]


class DtlsClient:
    """The handshake state and keys of one client address."""

    def __init__(self) -> None:
        """Before the cookie hello."""
        self.client_random = b""
        self.server_random = b""
        self.transcript = bytearray()
        self.master_secret = b""
        self.client_aead: AESGCM | None = None
        self.client_iv = b""
        self.server_aead: AESGCM | None = None
        self.server_iv = b""
        self.send_epoch = 0
        self.send_seq = 0
        self.message_seq = 0
        self.identity_ok = False
        self.established = False

    def derive_keys(self, psk: bytes) -> None:
        """The PSK pre-master secret, master secret and key block (RFC 4279)."""
        n = struct.pack("!H", len(psk))
        pre_master = n + b"\x00" * len(psk) + n + psk
        self.master_secret = prf(
            pre_master, b"master secret", self.client_random + self.server_random, 48
        )
        block = prf(
            self.master_secret,
            b"key expansion",
            self.server_random + self.client_random,
            40,
        )
        self.client_aead, self.server_aead = AESGCM(block[0:16]), AESGCM(block[16:32])
        self.client_iv, self.server_iv = block[32:36], block[36:40]


class DtlsServer:
    """Answers the handshake and decrypts the HueStream messages."""

    def __init__(self, bridge: Bridge, port: int) -> None:
        """Bind UDP on 127.0.0.1 only."""
        self._bridge = bridge
        self._events = bridge.events
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((HOST, port))
        self._cookie_secret = os.urandom(16)
        self._clients: dict[tuple[str, int], DtlsClient] = {}

    def serve_forever(self) -> None:
        """Handle datagrams until the process ends."""
        while True:
            data, address = self._sock.recvfrom(65535)
            try:
                self._datagram(data, address)
            except Exception as err:  # noqa: BLE001 - a bad datagram never stops it
                self._error(address, f"{type(err).__name__}: {err}")

    def _datagram(self, data: bytes, address: tuple[str, int]) -> None:
        offset = 0
        while offset + 13 <= len(data):
            content_type, version, epoch = struct.unpack_from("!BHH", data, offset)
            length = int.from_bytes(data[offset + 11 : offset + 13], "big")
            header = data[offset : offset + 13]
            fragment = data[offset + 13 : offset + 13 + length]
            offset += 13 + length
            if version != _VERSION:
                self._error(address, f"record version {version:#06x}")
                continue
            if content_type == _CT_HANDSHAKE and epoch == 0:
                self._handshake(fragment, address)
            elif content_type == _CT_CHANGE_CIPHER_SPEC:
                pass  # the client's keys apply from its next record (epoch 1)
            elif epoch == 1:
                self._encrypted(content_type, header, fragment, address)
            else:
                self._error(address, f"unexpected record type {content_type}")

    def _handshake(self, fragment: bytes, address: tuple[str, int]) -> None:
        msg_type = fragment[0]
        length = int.from_bytes(fragment[1:4], "big")
        message, body = fragment[: 12 + length], fragment[12 : 12 + length]
        if msg_type == _HT_CLIENT_HELLO:
            self._client_hello(message, body, address)
            return
        client = self._clients.get(address)
        if client is None:
            self._error(address, "handshake message before the hello")
            return
        if msg_type == _HT_CLIENT_KEY_EXCHANGE:
            identity = body[2 : 2 + int.from_bytes(body[0:2], "big")]
            # Compared, never logged or recorded.
            psk = next(
                (
                    client_key
                    for key, client_key in CLIENT_KEYS.items()
                    if hmac.compare_digest(identity, key.encode())
                ),
                None,
            )
            client.identity_ok = psk is not None
            client.transcript += message
            client.derive_keys(psk or CLIENT_KEYS[APP_KEY])
        else:
            self._error(address, f"unexpected handshake message {msg_type}")

    def _client_hello(
        self, message: bytes, body: bytes, address: tuple[str, int]
    ) -> None:
        client_random = body[2:34]
        session_id_length = body[34]
        at = 35 + session_id_length
        cookie = body[at + 1 : at + 1 + body[at]]
        at += 1 + body[at]
        suites = body[at + 2 : at + 2 + int.from_bytes(body[at : at + 2], "big")]
        expected = hmac.new(
            self._cookie_secret, repr(address).encode(), hashlib.sha256
        ).digest()[:16]
        if not cookie:
            # HelloVerifyRequest: the client repeats its hello with the cookie.
            self._clients.pop(address, None)
            self._send_handshake(
                DtlsClient(),
                _HT_HELLO_VERIFY_REQUEST,
                struct.pack("!HB", _VERSION, len(expected)) + expected,
                address,
            )
            return
        if not hmac.compare_digest(cookie, expected):
            self._error(address, "wrong cookie")
            return
        if struct.pack("!H", _CIPHER_SUITE) not in [
            suites[i : i + 2] for i in range(0, len(suites), 2)
        ]:
            self._error(address, "the client does not offer the PSK cipher suite")
            return
        # A resent hello starts the handshake again.
        client = self._clients[address] = DtlsClient()
        client.client_random = client_random
        client.server_random = struct.pack("!I", int(time.time())) + os.urandom(28)
        client.transcript += message
        client.message_seq = 1
        server_hello = (
            struct.pack("!H", _VERSION)
            + client.server_random
            + b"\x00"  # no session id
            + struct.pack("!HB", _CIPHER_SUITE, 0)  # null compression
        )
        # ServerHello and ServerHelloDone in one datagram, as the client reads
        # them from one (it waits for a second one otherwise).
        hello = self._handshake_message(client, _HT_SERVER_HELLO, server_hello)
        done = self._handshake_message(client, _HT_SERVER_HELLO_DONE, b"")
        client.transcript += hello + done
        self._sock.sendto(
            self._record(client, _CT_HANDSHAKE, hello)
            + self._record(client, _CT_HANDSHAKE, done),
            address,
        )

    def _encrypted(
        self,
        content_type: int,
        header: bytes,
        fragment: bytes,
        address: tuple[str, int],
    ) -> None:
        client = self._clients.get(address)
        if client is None or client.client_aead is None:
            self._error(address, "encrypted record without a handshake")
            return
        explicit_nonce, sealed = fragment[:8], fragment[8:]
        aad = explicit_nonce + struct.pack(
            "!BHH", content_type, _VERSION, len(sealed) - 16
        )
        try:
            plaintext = client.client_aead.decrypt(
                client.client_iv + explicit_nonce, sealed, aad
            )
        except InvalidTag:
            self._error(address, "a record that does not decrypt (wrong key)")
            return
        if content_type == _CT_HANDSHAKE and plaintext[0] == _HT_FINISHED:
            self._finished(client, plaintext, address)
        elif content_type == _CT_APPLICATION_DATA and client.established:
            self._huestream(plaintext, address)
        else:
            self._error(address, f"unexpected encrypted record type {content_type}")

    def _finished(
        self, client: DtlsClient, message: bytes, address: tuple[str, int]
    ) -> None:
        verify = prf(
            client.master_secret,
            b"client finished",
            hashlib.sha256(client.transcript).digest(),
            12,
        )
        finished_ok = hmac.compare_digest(message[12:24], verify)
        client.established = client.identity_ok and finished_ok
        self._events.add(
            "handshake",
            client=address[1],
            identity_ok=client.identity_ok,
            finished_ok=finished_ok,
        )
        if not client.established:
            return
        client.transcript += message
        # ChangeCipherSpec, then our Finished, each in its own datagram, as
        # the client reads them; without them it waits 3 s before streaming.
        self._sock.sendto(
            self._record(client, _CT_CHANGE_CIPHER_SPEC, b"\x01"), address
        )
        client.send_epoch, client.send_seq = 1, 0
        verify = prf(
            client.master_secret,
            b"server finished",
            hashlib.sha256(client.transcript).digest(),
            12,
        )
        finished = self._handshake_message(client, _HT_FINISHED, verify)
        self._sock.sendto(self._sealed(client, _CT_HANDSHAKE, finished), address)

    def _huestream(self, message: bytes, address: tuple[str, int]) -> None:
        """Record one HueStream v2 message: the area and each channel's colour."""
        if message[:9] != b"HueStream" or message[9:11] != b"\x02\x00":
            self._error(address, "not a HueStream v2 message")
            return
        area = message[HUESTREAM_HEADER : HUESTREAM_HEADER + AREA_ID_LENGTH].decode(
            "ascii", "replace"
        )
        body = message[HUESTREAM_HEADER + AREA_ID_LENGTH :]
        channels: dict[str, list[int]] = {}
        # 7 bytes per channel: its id, then each colour as 16 bits. Only the
        # high byte is kept: hue-entertainment writes an 8-bit value twice and
        # shifts a 16-bit one down, so the wire cannot tell the two apart
        # (Glowcast's 8-bit rule is covered by the unit tests).
        for at in range(0, len(body) - 6, 7):
            channel, red, _, green, _, blue, _ = body[at : at + 7]
            channels[str(channel)] = [red, green, blue]
        self._events.add(
            "stream",
            client=address[1],
            area=area,
            active=area == self._bridge.active_area,
            sequence=message[11],
            color_space=message[14],
            whole=len(body) % 7 == 0,
            channels=channels,
        )

    def _handshake_message(
        self, client: DtlsClient, msg_type: int, body: bytes
    ) -> bytes:
        length = len(body).to_bytes(3, "big")
        message = (
            struct.pack(
                "!B3sH3s3s", msg_type, length, client.message_seq, b"\0\0\0", length
            )
            + body
        )
        client.message_seq += 1
        return message

    def _send_handshake(
        self, client: DtlsClient, msg_type: int, body: bytes, address: tuple[str, int]
    ) -> None:
        message = self._handshake_message(client, msg_type, body)
        self._sock.sendto(self._record(client, _CT_HANDSHAKE, message), address)

    def _record(self, client: DtlsClient, content_type: int, fragment: bytes) -> bytes:
        header = struct.pack(
            "!BHH6sH",
            content_type,
            _VERSION,
            client.send_epoch,
            client.send_seq.to_bytes(6, "big"),
            len(fragment),
        )
        client.send_seq += 1
        return header + fragment

    def _sealed(self, client: DtlsClient, content_type: int, plaintext: bytes) -> bytes:
        assert client.server_aead is not None
        explicit_nonce = struct.pack(
            "!H", client.send_epoch
        ) + client.send_seq.to_bytes(6, "big")
        aad = explicit_nonce + struct.pack(
            "!BHH", content_type, _VERSION, len(plaintext)
        )
        sealed = client.server_aead.encrypt(
            client.server_iv + explicit_nonce, plaintext, aad
        )
        return self._record(client, content_type, explicit_nonce + sealed)

    def _error(self, address: tuple[str, int], reason: str) -> None:
        self._events.add("dtls_error", client=address[1], reason=reason)
        print(f"fake bridge: DTLS error: {reason}", flush=True)


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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("port", type=int)
    parser.add_argument("--dtls-port", type=int)
    args = parser.parse_args()
    cert, key = make_key(args.directory)
    bridge = Bridge(Events(args.directory / "events.jsonl"))
    server = http.server.ThreadingHTTPServer((HOST, args.port), make_handler(bridge))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    # The handshake runs in each request's own thread, not in accept(), so a
    # stalled client cannot block the others.
    server.socket = context.wrap_socket(
        server.socket, server_side=True, do_handshake_on_connect=False
    )
    if args.dtls_port is not None:
        dtls = DtlsServer(bridge, args.dtls_port)
        threading.Thread(target=dtls.serve_forever, daemon=True).start()
        print(f"fake bridge: DTLS on udp://{HOST}:{args.dtls_port}", flush=True)
    print(f"fake bridge: listening on https://{HOST}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
