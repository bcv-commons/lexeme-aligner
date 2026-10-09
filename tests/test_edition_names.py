"""Edition names become folder names in every compact repo and base_text values in the pooled datasets: they must be path-safe."""
import json
from pathlib import Path

import pytest

from lexeme_aligner.compact_align import SAFE_EDITION, edition_id


def test_every_edition_name_in_sources_json_is_path_safe():
    src = json.loads(Path("config/sources.json").read_text(encoding="utf-8"))
    bad = {t: v["edition"] for t, v in src.items() if isinstance(v, dict) and v.get("edition") and not SAFE_EDITION.fullmatch(v["edition"])}
    assert bad == {}


def test_edition_id_refuses_an_unsafe_name():
    assert edition_id("eng", "bsb", {"bsb": {"edition": "BSB"}}) == "eng_BSB"
    with pytest.raises(ValueError):
        edition_id("hau", "hau_bib", {"hau_bib": {"edition": "HAUBIB (Hausa Contemporary Bible / OHCB)"}})
