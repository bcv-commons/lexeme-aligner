import json

from lexeme_aligner import split_detect as SD
from lexeme_aligner import split_gen as SG
from lexeme_aligner import target_parts as TP


def test_family_detection_uses_the_glottolog_classification():
    assert SG.family_of("zin") == "bantu" and SG.family_of("swh") == "bantu"
    assert SG.family_of("yor") is None            # Atlantic-Congo but not Narrow Bantu
    assert SG.family_of("kbq") == "tng" and SG.family_of("eng") is None


def _setup(tmp_path, monkeypatch, entries):
    cfg = tmp_path / "config" / "target_parts.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(SD, "CONFIG", cfg)
    monkeypatch.setattr(SD, "REPO", tmp_path)
    return cfg


def test_hand_entries_are_never_touched(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"zin_wbt": {"chain": True, "dataset": "bcv-commons/language-analysis"}})
    assert "hand-made" in SD.decide_skip("zin_wbt", "zin", force=True, stale=False)


def test_write_entry_and_the_chain_reads_it(tmp_path, monkeypatch):
    cfg = _setup(tmp_path, monkeypatch, {"heb_scg": {"dataset": "x"}})
    split = tmp_path / "pipeline/generated-splits/ab_x/split/words.parquet"
    split.parent.mkdir(parents=True)
    split.write_bytes(b"parquet bytes")
    info = {"family": "bantu", "template": "config/split_families/bantu.json", "template_sha256": "f" * 64, "split_gen": "0.1",
            "split_detect": SD.VERSION}
    res = {"checked": "2026-11-28", "verdict": "split", "delta": {"content_stable": 0.01}, "figures": {}, "thresholds": {},
           "books": "MRK", "runs": 2}
    SD.write_entry("ab_x", "abc", res, split, info)
    d = json.loads(cfg.read_text(encoding="utf-8"))
    e = d["ab_x"]
    assert e["source"] == SD.SOURCE and e["chain"] is True and e["local"] == "pipeline/generated-splits/ab_x/split/words.parquet"
    assert d["heb_scg"] == {"dataset": "x"}
    monkeypatch.setattr(TP, "__file__", str(tmp_path / "pipeline/lexeme_aligner/target_parts.py"))
    local, entry = TP.for_edition("ab_x", config=cfg)
    assert local == split and entry["sha256"] == e["sha256"]
    SD.write_entry("ab_x", "abc", res | {"verdict": "words"}, split, info)
    assert json.loads(cfg.read_text(encoding="utf-8"))["ab_x"]["chain"] is False
    assert TP.for_edition("ab_x", config=cfg) is None       # chain false: the chain keeps whole words
