import socket

import pytest

from lexeme_aligner import dns_cache as dc


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "CACHE", tmp_path / "dns.json")
    monkeypatch.setattr(dc, "_mem", None)
    state = {"fail": False}
    real_results = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443))]

    def fake_real(host, port, *a, **k):
        if state["fail"]:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        return real_results

    monkeypatch.setattr(dc, "_real", fake_real)
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)             # restored by monkeypatch after the test
    monkeypatch.delenv("ALIGNER_NO_DNS_CACHE", raising=False)
    monkeypatch.setattr(socket.getaddrinfo, "_aligner_dns_cache", False, raising=False)
    dc.install()
    return state


def test_a_failed_lookup_falls_back_to_the_last_good_answer(isolated):
    assert socket.getaddrinfo("api.example.org", 443)[0][4] == ("203.0.113.7", 443)       # success is remembered
    isolated["fail"] = True
    assert socket.getaddrinfo("api.example.org", 443)[0][4] == ("203.0.113.7", 443)       # DNS down -> remembered address
    assert socket.getaddrinfo("api.example.org", 8443)[0][4] == ("203.0.113.7", 8443)     # the requested port is honoured


def test_an_unknown_host_still_fails_normally(isolated):
    isolated["fail"] = True
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("never-resolved.example.org", 443)


def test_a_working_resolver_always_wins_and_refreshes_the_cache(isolated, monkeypatch):
    socket.getaddrinfo("api.example.org", 443)
    monkeypatch.setattr(dc, "_real", lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.9", 443))])
    assert socket.getaddrinfo("api.example.org", 443)[0][4][0] == "198.51.100.9"
    monkeypatch.setattr(dc, "_real", lambda h, p, *a, **k: (_ for _ in ()).throw(socket.gaierror(-3, "down")))
    assert socket.getaddrinfo("api.example.org", 443)[0][4][0] == "198.51.100.9"           # the NEWER address was kept


def test_the_cache_is_shared_through_the_file(isolated, tmp_path, monkeypatch):
    socket.getaddrinfo("api.example.org", 443)
    monkeypatch.setattr(dc, "_mem", None)                                                  # a fresh process: nothing in memory
    isolated["fail"] = True
    assert socket.getaddrinfo("api.example.org", 443)[0][4][0] == "203.0.113.7"


def test_ip_literals_are_not_cached(isolated):
    socket.getaddrinfo("203.0.113.7", 443)
    assert "203.0.113.7" not in dc._load()
