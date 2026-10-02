"""Last-known-good DNS fallback (2026-10-01).

This machine's resolver chain goes flaky for minutes at a time (lookups of 2-20 s, then "Temporary failure in name resolution"; systemd-resolved
drops to degraded UDP/TCP modes), which aborted whole language chains (xtm, ydd) and cost the sweep dozens of retries. `install()` wraps
`socket.getaddrinfo`: a SUCCESSFUL lookup is remembered (a small JSON file shared by every process); only when a lookup FAILS with `gaierror` is
the remembered answer used. Safe by design: TLS still verifies the hostname against the certificate, so a stale or wrong address can only fail to
connect, never reach the wrong server; and a working resolver is always preferred, so a changed address is picked up as soon as DNS answers.
Disable with ALIGNER_NO_DNS_CACHE=1. Installed from lexeme_aligner.config, i.e. by every aligner process started after this change.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from pathlib import Path

CACHE = Path(os.environ.get("ALIGNER_DNS_CACHE", Path(__file__).resolve().parents[2] / "pipeline/work/dns_cache.json"))
_lock = threading.Lock()
_mem: dict[str, list] | None = None
_real = socket.getaddrinfo


def _load() -> dict[str, list]:
    global _mem
    if _mem is None:
        try:
            _mem = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _mem = {}
    return _mem


def _remember(host: str, res: list) -> None:
    entries = [[int(f), int(t), int(p), c, list(sa)] for f, t, p, c, sa in res if isinstance(sa, tuple)]
    if not entries:
        return
    with _lock:
        mem = _load()
        if mem.get(host) == entries:
            return
        mem[host] = entries
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(CACHE.parent), prefix=".dns_cache.")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(mem, fh)
            os.replace(tmp, CACHE)
        except OSError:
            pass                                                            # a cache that cannot be saved is just not a cache


def _cached(host: str, port) -> list | None:
    entries = _load().get(host)
    if not entries:
        return None
    out = []
    for f, t, p, c, sa in entries:
        sa = list(sa)
        try:
            sa[1] = int(port) if port is not None else sa[1]
        except (TypeError, ValueError):
            pass
        out.append((socket.AddressFamily(f), socket.SocketKind(t), p, c, tuple(sa)))
    return out


def install() -> None:
    if os.environ.get("ALIGNER_NO_DNS_CACHE") or getattr(socket.getaddrinfo, "_aligner_dns_cache", False):
        return

    def getaddrinfo(host, port, *args, **kwargs):
        try:
            res = _real(host, port, *args, **kwargs)
        except socket.gaierror:
            if isinstance(host, str):
                hit = _cached(host, port)
                if hit:
                    return hit
            raise
        if isinstance(host, str) and not host.replace(".", "").isdigit() and ":" not in host:
            _remember(host, res)
        return res

    getaddrinfo._aligner_dns_cache = True                                   # type: ignore[attr-defined]
    socket.getaddrinfo = getaddrinfo
