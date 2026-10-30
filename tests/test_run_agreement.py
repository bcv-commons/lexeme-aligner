"""run_agreement: per aligned entry, how many repeated chain runs made exactly that link."""
import json

from lexeme_aligner import run_agreement as ra


def test_counts_entry_by_entry():
    main = ["0:1 1:3-4", "", "2:5"]
    assert ra.counts(main, [["0:1 1:3", "", "2:5"], ["0:1", "0:9", ""]]) == ["31", "", "2"]


def test_annotate_adds_the_channel_and_the_manifest_count(tmp_path):
    def book(root, strings):
        d = root / "z" / "zin" / "zin-m_wbt"
        d.mkdir(parents=True, exist_ok=True)
        (d / "PHM_abcd1234.json").write_text(json.dumps(strings))
        return d
    d1 = book(tmp_path / "a", ["0:1 1:2"])
    (d1 / "PHM_abcd1234.meta.json").write_text(json.dumps({"conf": ["11"]}))
    (tmp_path / "a" / "manifest.json").write_text(json.dumps({"languages": {"zin": {"editions": {"zin-m_wbt": {"books": ["PHM"]}}}}}))
    book(tmp_path / "b", ["0:1 1:3"])
    rep = ra.annotate(tmp_path / "a", [tmp_path / "b"])
    meta = json.loads((d1 / "PHM_abcd1234.meta.json").read_text())
    assert meta == {"conf": ["11"], "runs": ["21"]} and rep["by_count"] == {"1": 1, "2": 1}
    assert json.loads((tmp_path / "a" / "manifest.json").read_text())["languages"]["zin"]["editions"]["zin-m_wbt"]["runs"] == 2
