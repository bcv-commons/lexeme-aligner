"""Config — every external path in ONE place, env-overridable.

This is the aligner's only coupling to its host layout. Defaults point at the repo's `pipeline/`
directory (spine.db lives there, gitignored — see config/PROVENANCE.txt); set the env vars to run
against a different spine, published bcv-commons datasets, or a spine built from STEPBible+MACULA.
See DATA.md. Repo layout (2026-07 reorg): `publish/` (staged HF datasets), `config/` (small tracked
config/pins), `pipeline/` (this package + vendor snapshots + transient `work/`).

  ALIGNER_SPINE_DB   original-language backbone (spine_words: book,chapter,verse,idx,surface,strong,lemma,morph,is_content)
  ALIGNER_HBO_DB     per-occurrence sense sidecar (occurrence: ref,lex,stem,sp,strong,gloss,sense,sense_conf) — optional
  ALIGNER_RESOURCES  dir holding gloss priors (word_glosses/, llm_strongs_glosses/, strongs_tw.tsv, tw_articles/) — optional
  ALIGNER_OUT        experiment output dir (gitignored)
  ALIGNER_HF_CHUNK_SIZE  files per HF commit for every publish_chunked() call project-wide (default
                     200 — HF's 128/hour-per-repo commit-rate limit means fewer, bigger commits go
                     further, but very large chunks have been observed to time out on this
                     connection; lower it, e.g. 100, if a publish run starts hitting a ReadTimeout/
                     WriteTimeout — no code change needed, just re-run with the env var set)
"""
from __future__ import annotations

import os
from pathlib import Path

# _PIPELINE = pipeline/ (parent of this lexeme_aligner/ package dir) — spine.db, vendor/, work/ all
# live there. _REPO_ROOT = the actual repo root, one level further up — publish/ lives there.
_PIPELINE = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PIPELINE.parent


def _p(env: str, default: Path) -> Path:
    v = os.environ.get(env)
    return Path(v) if v else default


SPINE_DB = _p("ALIGNER_SPINE_DB", _PIPELINE / "lexeme-spine.db")  # required — lexeme-anchored (see config/PROVENANCE.txt)
HBO_DB = _p("ALIGNER_HBO_DB", _PIPELINE / "hbo.db")               # optional — per-occurrence sense sidecar
RESOURCES = _p("ALIGNER_RESOURCES", _PIPELINE / "vendor" / "resources")  # optional — gloss priors (bcv-commons/strongs)
OUT = _p("ALIGNER_OUT", _PIPELINE / "work" / "out")               # experiment output (gitignored, transient)
LEX_ROOT = _p("ALIGNER_LEX_ROOT", _REPO_ROOT / "publish" / "lexeme-alignments")  # published dataset root (was aligned_lex)
# language-independent prior pack pulled from bcv-commons/prior-pack (HF, CC-BY) — feeds the recipes
# (R1 keyness-filter, R2 sense-surface, R3 gap-map, LXX NT-gap). See internal-docs/aligner-handover.md.
PRIOR_PACK = _p("ALIGNER_PRIOR_PACK", _PIPELINE / "vendor" / "prior-pack" / "prior_pack.parquet")
HF_CHUNK_SIZE = int(os.environ.get("ALIGNER_HF_CHUNK_SIZE", "200"))  # files per HF commit, project-wide
