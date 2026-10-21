"""Owner rule 2026-10-21: a full-aligned edition is published only when its text has an open licence (PD, CC0, any CC incl. NC/ND);
config/fullalign_publish.json `hold` keeps a layer local whatever its licence (pipeline/scripts/publish_safe.py)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publish_safe_gate", ROOT / "pipeline/scripts/publish_safe.py")
ps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ps)

CFG = {"open_licence": r"public\s*domain|\bcc0\b|\bcc[- ]?by\b|creative\s*commons", "text_licences": {"edX": {"licence": "CC BY-NC-ND 4.0"}},
       "hold": {"edH+llm+v": {"reason": "owner"}}}


def _repo(tmp_path):
    (tmp_path / "config/pins").mkdir(parents=True)
    (tmp_path / "config/edition_struct").mkdir(parents=True)
    (tmp_path / "config/pins/pdtag.json").write_text(json.dumps({"license": "Public Domain"}))
    (tmp_path / "config/pins/ctag.json").write_text(json.dumps({"copyright": "© 1995 Some Society"}))
    (tmp_path / "config/edition_struct/ebtag.json").write_text(json.dumps({"derived": {"ebible": {"license": "public domain"}}}))
    return tmp_path


def test_text_licence_sources(tmp_path):
    repo = _repo(tmp_path)
    tags = {"edPD": "pdtag", "edC": "ctag", "edE": "ebtag"}
    assert ps.text_licence("edX", CFG, tags, repo) == "CC BY-NC-ND 4.0"          # config wins
    assert ps.text_licence("edPD", CFG, tags, repo) == "Public Domain"           # pin license
    assert ps.text_licence("edE", CFG, tags, repo) == "public domain"           # eBible public-domain line
    assert ps.text_licence("edC", CFG, tags, repo) == ""                         # a copyright line names no licence


def test_unpublishable_layers_and_drop(tmp_path):
    repo = _repo(tmp_path)
    tags = {"edPD": "pdtag", "edC": "ctag", "edH": "pdtag"}
    local = {"layout": "x", "languages": {
        "aaa": {"layers": {"edPD+llm+v": {"edition": "edPD"}, "edX+manual+s": {"edition": "edX"}}},
        "bbb": {"layers": {"edC+llm+v": {"edition": "edC"}}},
        "ccc": {"layers": {"edH+llm+v": {"edition": "edH"}}}}}
    why = ps.unpublishable_layers(local, CFG, tags, repo)
    assert set(why) == {"edC+llm+v", "edH+llm+v"} and why["edH+llm+v"].startswith("hold")
    kept = ps.drop_held(local, set(why))
    assert kept["layout"] == "x" and list(kept["languages"]) == ["aaa"]          # a language with no layer left is dropped


def test_selected_files_leave_out_held_layers(tmp_path):
    for lid in ("edA+llm+v", "edH+llm+v"):
        d = tmp_path / "a/aaa" / lid
        d.mkdir(parents=True)
        (d / "GEN_1a2b3c4d.json").write_text("[]")
    assert ps.selected_files("full-alignments-llm", "fullalign", ["aaa"], tmp_path, {"edH+llm+v"}) == ["a/aaa/edA+llm+v/GEN_1a2b3c4d.json"]


def test_the_real_config_parses_and_holds_hebm95():
    cfg = json.loads(ps.FA_PUBLISH.read_text(encoding="utf-8"))
    assert "HEBM95+llm+jev-v4" in cfg["hold"]
    assert all("licence" in v and "source" in v for v in cfg["text_licences"].values())
