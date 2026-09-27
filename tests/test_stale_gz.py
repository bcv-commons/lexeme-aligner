"""2026-09-27: a fresh align_*.jsonl writer must remove the `.jsonl.gz` sibling a previous
`full_chain --clean-out` left, or every downstream step of the re-run reads old+new together
(spa: 1,219,951 in-chain rows vs 977,411 clean)."""
import gzip
import json

import lexeme_aligner.align_files as af


def test_unlink_stale_gz_removes_sibling(tmp_path):
    fresh = tmp_path / "align_eflomal_x_GEN.jsonl"
    stale = tmp_path / "align_eflomal_x_GEN.jsonl.gz"
    with gzip.open(stale, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"ref": 1, "pairs": []}) + "\n")
    assert af.unlink_stale_gz(fresh) is True
    assert not stale.exists()
    assert af.unlink_stale_gz(fresh) is False          # nothing left to remove


def test_tag_files_would_have_returned_both_spellings(tmp_path):
    # documents WHY the unlink is needed: tag_files deliberately reads both spellings.
    (tmp_path / "align_eflomal_x_GEN.jsonl").write_text("", encoding="utf-8")
    with gzip.open(tmp_path / "align_eflomal_x_GEN.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write("")
    assert len(af.tag_files(tmp_path, "eflomal", "x")) == 2
    af.unlink_stale_gz(tmp_path / "align_eflomal_x_GEN.jsonl")
    assert len(af.tag_files(tmp_path, "eflomal", "x")) == 1
