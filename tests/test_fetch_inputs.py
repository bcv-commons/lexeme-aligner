"""fetch-inputs: the pinned list, sha256 checks, optional per-language files, the record (a local HTTP server, no network)."""
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from lexeme_aligner import align_text, fetch_inputs as fi


def test_the_shipped_list_names_every_input_align_text_reads():
    lst = fi.load_list()
    dests = {f["dest"] for f in lst["files"]}
    assert {"lexeme-spine-macula.db", "prior_pack.parquet", "gram_struct/article_bound.json"} <= dests
    for f in lst["files"]:
        assert f["licence"] and ("url" in f or {"repo", "revision", "path"} <= set(f))
    assert set(lst["gram_struct"]["partitions"]) == {"external", "imputed", "derived", "measured", "kin"}


def test_wanted_adds_grammar_files_per_language_and_jev_only_on_request():
    lst = fi.load_list()
    plain = fi.wanted(lst, ["kvg"], jev=False)
    assert not any(w["dest"].startswith("greek-nt-clause-layout") for w in plain)
    assert {w["dest"] for w in plain if w["optional"]} == {f"gram_struct/{p}/kvg.json" for p in lst["gram_struct"]["partitions"]}
    assert any(w["dest"] == "greek-nt-clause-layout/clauses.parquet" for w in fi.wanted(lst, [], jev=True))


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _list(base: str, sha: str) -> dict:
    return {"files": [{"name": "a", "dest": "a.bin", "url": f"{base}/a.bin", "sha256": sha, "licence": "CC0"}],
            "gram_struct": {"repo": "x/y", "revision": "r", "partitions": ["external"], "licences": {"default": "CC0"}}}


def test_fetch_checks_sha_records_and_skips_a_good_copy(tmp_path, server, monkeypatch):
    root, base = server
    (root / "a.bin").write_bytes(b"hello")
    (root / "external").mkdir()
    (root / "external" / "kvg.json").write_text("{}")
    monkeypatch.setattr(fi, "hf_url", lambda repo, rev, path: f"{base}/{path}")
    lst = _list(base, hashlib.sha256(b"hello").hexdigest())
    data = tmp_path / "data"
    rec = fi.fetch(data, ["kvg", "zzz"], False, lst=lst)
    assert (data / "a.bin").read_bytes() == b"hello" and (data / "gram_struct/external/kvg.json").exists()
    assert rec["files"]["gram_struct/external/zzz.json"]["absent"] and rec["isos"] == ["kvg", "zzz"]
    assert json.loads((data / "inputs.json").read_text())["files"]["a.bin"]["licence"] == "CC0"
    (root / "a.bin").unlink()                                   # a second run fetches nothing
    fi.fetch(data, ["kvg"], False, lst=lst)


def test_a_changed_upstream_file_is_refused_and_leaves_nothing(tmp_path, server):
    root, base = server
    (root / "a.bin").write_bytes(b"changed")
    with pytest.raises(SystemExit, match="upstream file changed"):
        fi.fetch(tmp_path / "data", [], False, lst=_list(base, hashlib.sha256(b"hello").hexdigest()))
    assert not (tmp_path / "data" / "a.bin").exists() and not (tmp_path / "data" / "a.bin.part").exists()


def test_align_text_reads_grammar_facts_from_the_data_folder(tmp_path):
    env = align_text.step_env(tmp_path, tmp_path / "data")
    assert env["ALIGNER_GRAM_STRUCT"] == str(tmp_path / "data" / "gram_struct")
    assert align_text.settings_path("config/gram_struct/external/<iso>.json", "kvg", tmp_path / "data") == \
        tmp_path / "data" / "gram_struct/external/kvg.json"
    assert align_text.settings_path("config/sources.json", "kvg", tmp_path / "data") == align_text.REPO / "config/sources.json"


def _db(fp: Path, extra_col: bool = False, word: str = "a") -> Path:
    import sqlite3
    con = sqlite3.connect(fp)
    con.execute("create table w (key text, surface text" + (", greek text" if extra_col else "") + ")")
    con.executemany("insert into w values (?, ?" + (", 'x'" if extra_col else "") + ")", [("2", "b"), ("1", word)])
    con.commit()
    con.close()
    return fp


CONTENT = {"rule": "tables-v1", "tables": [{"name": "w", "columns": ["key", "surface"], "order": ["key"]}]}


def test_content_sha_ignores_added_columns_but_not_changed_rows(tmp_path):
    base = fi.content_sha256(_db(tmp_path / "a.db"), CONTENT)
    assert base and fi.content_sha256(_db(tmp_path / "b.db", extra_col=True), CONTENT) == base
    assert fi.content_sha256(_db(tmp_path / "c.db", word="z"), CONTENT) != base
    assert fi.content_sha256(tmp_path / "a.db", {"tables": [{"name": "w", "columns": ["nope"], "order": ["key"]}]}) is None
    (tmp_path / "t.txt").write_text("not sqlite")
    assert fi.content_sha256(tmp_path / "t.txt", CONTENT) is None


def test_an_upstream_build_with_an_added_column_is_accepted_by_content(tmp_path, server):
    root, base = server
    pinned = _db(tmp_path / "pinned.db")
    _db(root / "a.bin", extra_col=True)
    lst = _list(base, hashlib.sha256(pinned.read_bytes()).hexdigest())
    lst["files"][0]["content"] = dict(CONTENT, sha256=fi.content_sha256(pinned, CONTENT))
    rec = fi.fetch(tmp_path / "data", [], False, lst=lst)
    assert rec["files"]["a.bin"]["accepted_by"] == "content"
    (root / "a.bin").unlink()
    _db(root / "a.bin", word="z")                                # a changed row: refused
    (tmp_path / "data" / "a.bin").unlink()
    with pytest.raises(SystemExit, match="content differs"):
        fi.fetch(tmp_path / "data", [], False, lst=lst)


def test_the_shipped_spine_pin_has_a_content_pin():
    spine = next(f for f in fi.load_list()["files"] if f["name"] == "spine")
    assert spine["content"]["sha256"] and spine["content"]["tables"][0]["order"] == ["key"]
