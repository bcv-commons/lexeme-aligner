"""P1 (2026-09-27): typed function-word lexicon learned from separate source function tokens."""
import json

import lexeme_aligner.function_classes as fc


def _pair(h_idx, lemma, target, t_idx, content=False):
    return {"h_idx": h_idx, "lemma": lemma, "target": target, "t_idx": t_idx, "content": content,
            "strong": "H0000", "lexeme": "hbo:0000", "score": 0.9, "method": "eflomal"}


def _write(tmp_path, tag, recs):
    fp = tmp_path / f"align_eflomal_{tag}_RUT.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_build_classes_groups_by_source_lemma_class(tmp_path):
    recs = [{"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
        *[_pair(i, "הַ", "el", [i]) for i in range(5)],
        *[_pair(10 + i, "הַ", "la", [10 + i]) for i in range(4)],
        *[_pair(20 + i, "וְ", "y", [20 + i]) for i in range(6)],
        *[_pair(30 + i, "בְּ", "en", [30 + i]) for i in range(3)],
        _pair(40, "הַ", "casa", [40], content=True),          # content flag -> ignored
        _pair(41, "הַ", "los", [41, 42]),                     # multi-target -> ignored
    ]}]
    _write(tmp_path, "fake", recs)
    classes = fc.build_function_classes("fake", tmp_path, min_count=3, min_share=0.0)
    assert set(classes["article"]) == {"el", "la"}
    assert abs(classes["article"]["el"] - 5 / 9) < 1e-4      # shares are rounded to 5 decimals
    assert set(classes["conjunction"]) == {"y"}
    assert set(classes["adposition"]) == {"en"}
    assert classes["pronoun"] == {} and classes["negation"] == {}


def test_min_count_drops_one_offs(tmp_path):
    recs = [{"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
        *[_pair(i, "הַ", "el", [i]) for i in range(5)], _pair(9, "הַ", "oops", [9])]}]
    _write(tmp_path, "fake", recs)
    classes = fc.build_function_classes("fake", tmp_path, min_count=2, min_share=0.0)
    assert "oops" not in classes["article"]


def test_function_classes_cache_and_lookup(tmp_path):
    recs = [{"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
        *[_pair(i, "הַ", "el", [i]) for i in range(4)],
        *[_pair(10 + i, "בְּ", "del", [10 + i]) for i in range(4)],
        *[_pair(20 + i, "הַ", "del", [20 + i]) for i in range(4)],       # 'del' in two classes
    ]}]
    _write(tmp_path, "fake", recs)
    f = fc.FunctionClasses("xx", "fake", tmp_path, cache_dir=tmp_path / "cache")
    assert (tmp_path / "cache" / "xx.json").exists()
    assert f.classes_of("EL") == {"article"}
    assert f.classes_of("del") == {"article", "adposition"}
    assert f.classes_of("casa") == set()
    assert f.allows("del", fc.TRIGGER_CLASSES["case_marking"]) is True
    assert f.allows("el", fc.TRIGGER_CLASSES["case_marking"]) is False
    assert f.allows("el", fc.TRIGGER_CLASSES["articles"]) is True
    # second construction reads the cache, no tag needed
    g = fc.FunctionClasses("xx", None, tmp_path, cache_dir=tmp_path / "cache")
    assert g.classes == f.classes


def test_every_trigger_maps_to_known_classes():
    known = set(fc.SOURCE_CLASSES)
    for trig, classes in fc.TRIGGER_CLASSES.items():
        assert classes <= known, trig
