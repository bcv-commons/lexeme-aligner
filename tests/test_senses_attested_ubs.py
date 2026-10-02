"""senses_attested --scheme ubs: the UBS-keyed attestation (synthetic alignment files + a synthetic binding table)."""
import json

import lexeme_aligner.senses_attested as sa


def _write(tmp_path, tag, recs, method="eflomal"):
    (tmp_path / f"align_{method}_{tag}_RUT.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _pair(h, lexeme, target, content=True, stem=""):
    return {"h_idx": h, "lexeme": lexeme, "target": target, "content": content, "stem": stem, "sense": "1"}


UBS = {("RUT", 1, 1, 3): ("S-father", "hbo:0001"), ("RUT", 1, 1, 5): ("S-family", "hbo:0001"),
       ("RUT", 1, 1, 7): ("S-in", "hbo:0871a"), ("RUT", 1, 1, 9): ("S-x", "hbo:0999")}


def test_ubs_scheme_keys_on_the_ubs_sense_and_keeps_function_words(tmp_path):
    rec = {"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
        _pair(3, "hbo:0001", "Father"), _pair(5, "hbo:0001", "families"),
        _pair(7, "hbo:0871a", "in", content=False),                     # a preposition: has a UBS sense, kept
        _pair(9, "hbo:0888", "zzz"),                                    # lexeme differs from the stored one: dropped
        _pair(11, "hbo:0001", "father")]}                               # no UBS binding at that index: dropped
    _write(tmp_path, "zz", [rec])
    counts, n = sa.aggregate(tmp_path, [("zz", "ed")], "eflomal", scheme="ubs", ubs=UBS)
    assert n == 1
    assert counts == {("hbo:0001", "", "S-father", "father", "ed", "eflomal"): 1,
                      ("hbo:0001", "", "S-family", "families", "ed", "eflomal"): 1,
                      ("hbo:0871a", "", "S-in", "in", "ed", "eflomal"): 1}


def test_legacy_scheme_is_unchanged_and_still_content_only(tmp_path):
    rec = {"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
        _pair(3, "hbo:0001", "father"), _pair(7, "hbo:0871a", "in", content=False)]}
    _write(tmp_path, "zz", [rec])
    counts, _ = sa.aggregate(tmp_path, [("zz", "ed")], "eflomal")
    assert list(counts) == [("hbo:0001", "", "1", "father", "ed", "eflomal")]


def test_share_is_within_each_ubs_sense(tmp_path):
    recs = [{"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [_pair(3, "hbo:0001", w)]}
            for w in ("father", "father", "fathers")]
    _write(tmp_path, "zz", recs)
    counts, _ = sa.aggregate(tmp_path, [("zz", "ed")], "eflomal", scheme="ubs", ubs=UBS)
    rows = sa.build_rows(counts, sa._totals(counts), "WLC", 1)
    by = {r[3]: r for r in rows}
    assert abs(by["father"][5] - 2 / 3) < 1e-9 and abs(by["fathers"][5] - 1 / 3) < 1e-9


def test_write_outputs_carry_the_ubs_column_name_and_license(tmp_path):
    rows = [("hbo:0001", "", "S-father", "father", 2, 1.0, "eflomal", "WLC", "ed")]
    sa.write_tsv(rows, tmp_path / "d.tsv", "ubs_sense")
    assert (tmp_path / "d.tsv").read_text(encoding="utf-8").splitlines()[0].split("\t")[2] == "ubs_sense"
    sa.update_manifest(tmp_path / "m.json", "xx", {"rows": 1}, "ubs")
    m = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert m["license"] == "cc-by-sa-4.0" and "United Bible Societies" in m["credit"] and m["schema"][2] == "ubs_sense"
    sa.update_manifest(tmp_path / "m2.json", "xx", {"rows": 1})
    assert "license" not in json.loads((tmp_path / "m2.json").read_text(encoding="utf-8"))


def test_the_ubs_dataset_card_states_the_share_alike_license_and_the_credit():
    assert "license: cc-by-sa-4.0" in sa._UBS_CARD and "United Bible Societies" in sa._UBS_CARD
    assert "SEPARATE dataset" in sa._UBS_CARD
