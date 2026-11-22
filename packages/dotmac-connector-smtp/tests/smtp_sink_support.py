"""Scripted SMTP sink and certificate helper shared by the connector tests.

Stdlib plus `cryptography` (already a dev dependency); loopback only; no real
network. Kept out of `conftest.py` so test modules import it by a plain module
name that cannot collide with the repository's root `conftest`.
"""

from __future__ import annotations

import base64
import datetime
import socket
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

TLS_HOST = "mail.example.test"
OTHER_HOST = "other.example.test"
#: Throwaway test credentials (the sink accepts GOOD_PW, rejects anything else).
GOOD_PW = "pw-ok"
BAD_PW = "wrong-pw-value"


class SmtpSink:
    def __init__(self, certfile: str, keyfile: str) -> None:
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(certfile, keyfile)
        self.advertise_starttls = True
        self.greeting = "220 sink ESMTP"
        self.replies: dict[str, str] = {}
        self.drop_after_data = False
        #: Slow-trickle modes: one byte every 0.2 s, never a line end, so every
        #: per-operation timeout is reset forever (up to 30 s).
        self.trickle_greeting = False
        self.trickle_data_reply = False
        self.accepted_secret = GOOD_PW
        self.messages: list[bytes] = []
        self.commands: list[str] = []
        self.helo_names: list[str] = []
        self.connections = 0
        self.auth_seen = False
        self.plaintext_auth_seen = False
        self.tls_versions: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        tls = False
        try:
            f = conn.makefile("rwb", buffering=0)
            if self.trickle_greeting:
                self._trickle(f)
                return
            f.write((self.greeting + "\r\n").encode())
            if not self.greeting.startswith("220"):
                return
            while True:
                line = f.readline()
                if not line:
                    return
                cmd = line.decode().rstrip("\r\n")
                verb = cmd.split(" ", 1)[0].upper()
                self.commands.append(verb)
                if verb == "EHLO":
                    self.helo_names.append(cmd.split(" ", 1)[1])
                if verb == "EHLO":
                    lines = ["250-sink", "250-AUTH PLAIN"]
                    if self.advertise_starttls and not tls:
                        lines.append("250-STARTTLS")
                    lines.append("250 8BITMIME")
                    f.write(("\r\n".join(lines) + "\r\n").encode())
                elif verb == "STARTTLS":
                    f.write(b"220 go ahead\r\n")
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                    self.tls_versions.append(conn.version() or "")
                    f = conn.makefile("rwb", buffering=0)
                    tls = True
                elif verb == "AUTH":
                    self.auth_seen = True
                    if not tls:
                        self.plaintext_auth_seen = True
                    cred = base64.b64decode(cmd.split()[2]).split(b"\0")[-1].decode()
                    ok = cred == self.accepted_secret
                    f.write(b"235 ok\r\n" if ok else b"535 5.7.8 bad credentials\r\n")
                elif verb == "MAIL":
                    f.write((self.replies.get("mail", "250 ok") + "\r\n").encode())
                elif verb == "RCPT":
                    f.write((self.replies.get("rcpt", "250 ok") + "\r\n").encode())
                elif verb == "DATA":
                    reply = self.replies.get("data_cmd", "354 go")
                    f.write((reply + "\r\n").encode())
                    if not reply.startswith("354"):
                        continue
                    buf = b""
                    while not buf.endswith(b"\r\n.\r\n"):
                        buf += f.readline()
                    self.messages.append(buf[:-5])
                    if self.trickle_data_reply:
                        self._trickle(f)
                        return
                    if self.drop_after_data:
                        conn.close()
                        return
                    reply = self.replies.get("data", "250 2.0.0 queued")
                    f.write((reply + "\r\n").encode())
                elif verb == "QUIT":
                    f.write(b"221 bye\r\n")
                    return
                else:
                    f.write(b"500 what\r\n")
        except (OSError, ssl.SSLError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _trickle(f) -> None:  # type: ignore[no-untyped-def]
        for _ in range(150):
            f.write(b"2")
            time.sleep(0.2)

    def close(self) -> None:
        self._stop = True
        self.sock.close()


def make_certificate(directory, name: str, dns_name: str) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{name}.crt", directory / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)
