"""compact-alignments is published as THREE repos that share one relative path scheme (2026-10-02).

The main repo (`bcv-commons/compact-alignments`) keeps everything a reader needs: README, manifest, `_index/`, tokenizer files, the ledger and every
`<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json` alignment array. The two optional, additive sidecar layers moved out because the single repo had
passed Hugging Face's recommended 100,000 files (180k on HF, ~205k expected; three files per book):

    <BOOK>_<hash>.meta.json   provenance sidecar (method / conf / contested / bonus)   -> bcv-commons/compact-alignments-meta
    <BOOK>_<hash>.extra.json  opt-in residual layer                                     -> bcv-commons/compact-alignments-extra
    <edition>/_layer.json     the full-align profile table the meta file's full-align channels decode with (2026-10-09) -> the meta repo

The relative path is IDENTICAL in all three repos, so a reader that wants a sidecar only needs a second base URL. Locally nothing changed: all three
file kinds still live side by side under publish/compact-alignments/; only the publishers route them (`split_layers`, `stage_layer`).
"""
from __future__ import annotations

import os
from pathlib import Path

LAYERS: dict[str, dict[str, str]] = {
    "meta": {"repo": "bcv-commons/compact-alignments-meta", "suffix": ".meta.json", "names": ("_layer.json",)},
    "extra": {"repo": "bcv-commons/compact-alignments-extra", "suffix": ".extra.json"},
}
MAIN_REPO = "bcv-commons/compact-alignments"
_REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = _REPO_ROOT / "pipeline/work/publish-staging"


def layer_of(rel_path: str) -> str:
    """'meta' / 'extra' for a sidecar file, else 'main'."""
    base = rel_path.rsplit("/", 1)[-1]
    for name, spec in LAYERS.items():
        if rel_path.endswith(spec["suffix"]) or base in spec.get("names", ()):
            return name
    return "main"


def split_layers(files: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"main": [], **{n: [] for n in LAYERS}}
    for f in files:
        out[layer_of(f)].append(f)
    return out


def layer_readme(layer: str) -> Path:
    """The tracked dataset card of a sidecar repo: publish/compact-alignments-<layer>/README.md."""
    return _REPO_ROOT / "publish" / f"compact-alignments-{layer}" / "README.md"


def stage_layer(src_root: Path, layer: str, rel_paths: list[str], staging: Path | None = None) -> tuple[Path, list[str]]:
    """Hard-link `rel_paths` (relative to src_root) plus the layer's own README.md into a persistent staging root and return (root, files to
    publish). The staging root keeps its `.publish_state.json` / `.publish_rate.json`, so a re-run skips what already went up. Hard links
    cost no disk; stale links from an earlier selection are harmless because only the returned file list is uploaded."""
    stage = staging or (STAGING / f"compact-alignments-{layer}")
    stage.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if os.path.samefile(dst, src_root / rel):
                continue
            dst.unlink()
        os.link(src_root / rel, dst)
    readme = layer_readme(layer)
    (stage / "README.md").write_bytes(readme.read_bytes())
    return stage, sorted(set(rel_paths) | {"README.md"})
