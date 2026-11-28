"""pipeline/split_engine: the standalone split engine (taken over from make-tanakh) splits as the published bundles' own copies."""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "pipeline/split_engine"
BUNDLES = sorted((REPO / "pipeline/vendor/language-analysis").glob("*/*/*/bundle"))
WORDS = ["naáyaandika", "Yesu", "atamushoboorora", "tibaamanyire", "omu", "ndøgo", "ndø-", "mæ-ne", "abantu"]


def _engine():
    spec = importlib.util.spec_from_file_location("bcv_segment_engine", ENGINE / "bcv_segment.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_sha256sums_match():
    for line in (ENGINE / "SHA256SUMS").read_text().splitlines():
        sha, name = line.split()
        assert hashlib.sha256((ENGINE / name).read_bytes()).hexdigest() == sha, name


def test_version_file_matches_module():
    assert (ENGINE / "VERSION").read_text().strip() == _engine().VERSION


def test_engine_has_no_outside_imports():
    src = (ENGINE / "bcv_segment.py").read_text(encoding="utf-8")
    assert "import grammar" not in src and "import register" not in src


@pytest.mark.skipif(not BUNDLES, reason="no vendored language-analysis bundles")
@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda b: "/".join(b.parts[-4:-1]))
def test_same_split_as_bundle_copy(bundle, tmp_path):
    words = tmp_path / "w.txt"
    words.write_text("\n".join(WORDS), encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "pipeline/scripts/tools/check_split_engine.py"), str(bundle), "--words", str(words)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["different"] == 0
