"""Local daemon authentication and handshake helpers."""

from __future__ import annotations

import errno
import json
import os
import secrets
import socket
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

TOKEN_ENV = "GLM_WATCHER_DAEMON_TOKEN"
HANDSHAKE_ENV = "GLM_WATCHER_DAEMON_HANDSHAKE"
DEFAULT_HANDSHAKE_PATH = Path("daemon.handshake.json")


@dataclass(frozen=True)
class DaemonHandshake:
    """Connection details for a local GUI process."""

    host: str
    port: int
    token: str

    def to_json(self) -> str:
        return json.dumps(
            {"host": self.host, "port": self.port, "token": self.token},
            ensure_ascii=False,
            indent=2,
        )


def generate_token() -> str:
    """Generate a per-launch bearer token."""

    return secrets.token_urlsafe(32)


def resolve_token(token: str | None = None) -> str:
    """Resolve explicit/env token, falling back to secure generation.

    An absent token never means anonymous access. It means "generate a local
    per-launch token and expose it only via the handshake file".
    """

    explicit = (token or "").strip()
    if explicit:
        return explicit

    env_value = os.environ.get(TOKEN_ENV, "").strip()
    if env_value:
        return env_value

    return generate_token()


def default_handshake_path() -> Path:
    """Return the default handshake file path, honoring the environment."""

    env_value = os.environ.get(HANDSHAKE_ENV, "").strip()
    return Path(env_value) if env_value else DEFAULT_HANDSHAKE_PATH


def write_handshake(path: str | Path, handshake: DaemonHandshake) -> None:
    """Write daemon host/port/token for the local shell.

    Best-effort chmod keeps the token readable only by the current user on
    POSIX systems. The token is per-launch and should not be committed.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(handshake.to_json() + "\n", encoding="utf-8")
    with suppress(OSError):
        target.chmod(0o600)


def allocate_free_port(host: str) -> int:
    """Ask the OS for an available TCP port on host."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def bind_server_socket(host: str, port: int) -> socket.socket:
    """Bind the daemon server socket before writing the handshake file."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(socket.SOMAXCONN)
    except OSError:
        sock.close()
        raise
    return sock


def bind_with_port_fallback(host: str, port: int) -> socket.socket:
    """Bind to the requested port; only fall back to a free port if it is in use.

    其它 OSError（host 无效、权限、socket 异常等）原样抛出，避免被误当成“端口占用”。
    port=0 本就是“自动选空闲端口”，失败时不应回退（多为底层异常）。
    """

    try:
        return bind_server_socket(host, port)
    except OSError as exc:
        if port == 0 or exc.errno != errno.EADDRINUSE:
            raise
        return bind_server_socket(host, 0)


def authorized_bearer(authorization: str | None, token: str) -> bool:
    """Return whether an Authorization header matches the daemon token."""

    if not authorization:
        return False
    scheme, _, value = authorization.partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(value.strip(), token)


def authorized_ws_token(query_token: str | None, subprotocol_header: str | None, token: str) -> bool:
    """Validate WebSocket token via query string or subprotocol header."""

    if query_token and secrets.compare_digest(query_token, token):
        return True

    if not subprotocol_header:
        return False

    for part in subprotocol_header.split(","):
        candidate = part.strip()
        if secrets.compare_digest(candidate, token):
            return True
        if candidate.lower().startswith("bearer.") and secrets.compare_digest(
            candidate[len("bearer.") :],
            token,
        ):
            return True
    return False
