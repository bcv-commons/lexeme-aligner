import collections
import gzip
import json

from lexeme_aligner import lexeme_glosses as lg


def _write(path, recs, gz=False):
    text = "".join(json.dumps(r) + "\n" for r in recs)
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _pair(h, lex, target, t_idx, content=True, method="eflomal"):
    return {"h_idx": h, "lexeme": lex, "target": target, "t_idx": t_idx, "content": content, "method": method, "score": 0.9}


def test_trim_keeps_top_and_drops_rare():
    c = collections.Counter({("hbo:0430", "gud"): 90, ("hbo:0430", "guds"): 8, ("hbo:0430", "x"): 1, ("hbo:0430", "y"): 2,
                             ("hbo:9999", "solo"): 1})
    t = lg.trim(c)
    assert t["hbo:0430"] == [["gud", 90], ["guds", 8]]          # y: 2 of 101 < 5%; x: once
    assert t["hbo:9999"] == [["solo", 1]]                       # the commonest form is always kept


def test_trim_caps_forms():
    c = collections.Counter({("hbo:0001", f"w{i}"): 10 for i in range(12)})
    assert len(lg.trim(c)["hbo:0001"]) == lg.MAX_FORMS


def test_count_edition_once_per_occurrence(tmp_path):
    # eflomal and gloss both link h_idx 1 (agreeing); spanext widens h_idx 2; h_idx 3 is a function token; one gz file
    _write(tmp_path / "align_eflomal_dan_x_RUT.jsonl.gz", [{"chapter": 1, "verse": 1, "pairs": [
        _pair(1, "hbo:0430", "Gud", [0]), _pair(2, "hbo:1004", "hus", [3]), _pair(3, "hbo:0853", "med", [4], content=False)]}], gz=True)
    _write(tmp_path / "align_gloss_dan_x_RUT.jsonl", [{"chapter": 1, "verse": 1, "pairs": [
        _pair(1, "hbo:0430", "Gud", [0], method="gloss")]}])
    _write(tmp_path / "align_spanext_dan_x_RUT.jsonl", [{"chapter": 1, "verse": 1, "pairs": [
        _pair(2, "hbo:1004", "Guds  hus", [2, 3], method="spanext")]}])
    fn = collections.Counter()
    c = lg.count_edition("dan_x", tmp_path, contest={}, function=fn)
    assert c == collections.Counter({("hbo:0430", "gud"): 1, ("hbo:1004", "guds hus"): 1, ("hbo:0853", "med"): 1})
    assert fn == {"hbo:0853": 1}


def test_function_list_needs_mostly_function_occurrences(tmp_path, monkeypatch):
    # hbo:5650 is a function token once (a Psalm heading forces content: false) in 3 occurrences; hbo:0853 always
    out = tmp_path / "out"
    out.mkdir()
    _write(out / "align_eflomal_dan_a_PSA.jsonl", [{"chapter": 18, "verse": 1, "pairs": [
        _pair(0, "hbo:5650", "tjener", [0], content=False), _pair(1, "hbo:5650", "tjener", [1]), _pair(2, "hbo:5650", "tjener", [2]),
        _pair(3, "hbo:0853", "med", [3], content=False)]}])
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"languages": {"dan": {"editions": {"dan_a": {}}}}}))
    monkeypatch.setattr(lg, "COMPACT_MANIFEST", man)
    lg.build("dan", out, tmp_path / "lex", rev="abc")
    assert json.loads((tmp_path / "lex/iso=dan/top.json").read_text())["function"] == ["hbo:0853"]


def test_build_pools_editions(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    for tag, word in (("dan_a", "gud"), ("danb", "Gud")):
        _write(out / f"align_eflomal_{tag}_RUT.jsonl", [{"chapter": 1, "verse": 1, "pairs": [_pair(0, "hbo:0430", word, [0])]}])
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"languages": {"dan": {"editions": {"dan_a": {}, "DANB": {"tag": "danb"}, "dan_c": {}}}}}))
    monkeypatch.setattr(lg, "COMPACT_MANIFEST", man)
    rep = lg.build("dan", out, tmp_path / "lex", rev="abc")
    doc = json.loads((tmp_path / "lex/iso=dan/top.json").read_text())
    assert doc["format"] == lg.FORMAT and doc["revision"] == "abc"
    assert doc["sources"] == ["DANB", "dan_a"]                  # published ids (DANB's files are tagged danb); dan_c has none
    assert doc["lexemes"] == {"hbo:0430": [["gud", 2]]} and doc["function"] == []
    assert rep["occurrences"] == 2


def test_edition_tags_reads_the_recorded_tag():
    from lexeme_aligner.compact_align import edition_tags
    doc = {"languages": {"eng": {"editions": {"eng_BSB": {"tag": "bsb"}, "ARBX": {"tag": "arbx"}, "eng_old": {}}}}}
    assert edition_tags("eng", doc) == {"eng_BSB": "bsb", "ARBX": "arbx", "eng_old": "eng_old"}
    assert edition_tags("xyz", doc) == {}
