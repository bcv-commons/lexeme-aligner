"""bcv-commons/bibles tools/verse-text 1.0.0: their conformance suite on our vendored copy (pipeline/vendor/bibles). The suite itself
(6.6 MB) is staged in pipeline/work/bibles-verse-text-1.0.0/ (git-ignored); the test is skipped when it is absent."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "pipeline/work/bibles-verse-text-1.0.0/python/conformance.py"
VENDOR = ROOT / "pipeline/vendor/bibles"


def test_the_vendored_release_is_1_0_0():
    assert (VENDOR / "VERSION").read_text().strip() == "1.0.0"


@pytest.mark.skipif(not SUITE.exists(), reason="conformance suite not staged in pipeline/work/bibles-verse-text-1.0.0")
def test_bibles_conformance_suite_passes_on_the_vendored_copy():
    r = subprocess.run([sys.executable, SUITE.name], cwd=SUITE.parent, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0 and "0 failures" in r.stdout, r.stdout[-800:] + r.stderr[-800:]
