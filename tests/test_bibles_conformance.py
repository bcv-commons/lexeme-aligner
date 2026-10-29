"""bcv-commons/bibles tools/verse-text 2.0.0: their conformance suite on our vendored copy (pipeline/vendor/bibles). The suite (their
conformance/ data + python/conformance.py + python/test_written.py, 6.7 MB) is staged in pipeline/work/bibles-verse-text-2.0.0/
(git-ignored); the scripts run in a temp folder beside OUR vendored modules, so the vendored copy is what gets checked. Skipped when
the suite is absent."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "pipeline/work/bibles-verse-text-2.0.0"
VENDOR = ROOT / "pipeline/vendor/bibles"


def test_the_vendored_release_is_2_0_0():
    assert (VENDOR / "VERSION").read_text().strip() == "2.0.0"


def _staged(tmp_path: Path) -> Path:
    py = tmp_path / "python"
    py.mkdir()
    for f in VENDOR.glob("*.py"):
        shutil.copy(f, py / f.name)
    for f in ("conformance.py", "test_written.py"):
        shutil.copy(SUITE / "python" / f, py / f)
    shutil.copytree(SUITE / "conformance", tmp_path / "conformance")
    return py


@pytest.mark.skipif(not (SUITE / "python/conformance.py").exists(), reason="conformance suite not staged in pipeline/work/bibles-verse-text-2.0.0")
def test_bibles_conformance_suite_passes_on_the_vendored_copy(tmp_path):
    py = _staged(tmp_path)
    r = subprocess.run([sys.executable, "-E", "-s", "conformance.py"], cwd=py, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0 and "0 failures" in r.stdout, r.stdout[-800:] + r.stderr[-800:]
    r = subprocess.run([sys.executable, "-E", "-s", "test_written.py"], cwd=py, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
