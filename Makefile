# lexeme-aligner — high-level workflow commands.
#
# Wraps the underlying scripts (onboard.py, full_chain.py, onboard_batch.py, onboard_catalog.py,
# export_lex.py/export_mwe.py/senses_attested.py/export_stopwords.py/export_morph.py/
# compact_align_batch.py, cross_lang_prior.py) so the six everyday actions don't need their
# individual flags memorized. Finer-grained calls to those scripts directly are still there for the
# rare cases that need them — see docs/architecture.md.
#
# THE 9-STEP CHAIN (what "new-language"/"update-language" actually runs — see full_chain.py):
#   1 ingest  2 eflomal align  3 export(eflomal-only, LOCAL)  4 gloss (bootstraps from step 3)
#   5 gapfill  6 export(final union, LOCAL)  7 aligned_mwe(LOCAL)  8 senses_attested(LOCAL)
#   9 compact-alignments(LOCAL)
# Every step writes LOCAL files only (a few KB-MB each) — publishing to Hugging Face is ALWAYS a
# separate, deliberate step (the `publish`/`publish-all`/`publish-span-profile` targets below),
# decoupled on purpose so HF's 128-commits/hour/repo limit is never a per-language concern.
#
# `out/`'s raw per-verse jsonl is transient — safe to delete once a language's chain finishes
# (CLEAN_OUT=1), but off by default: it's what a re-run of aligned_mwe/senses_attested/
# compact-alignments (e.g. with an improved algorithm later) would need without a full re-align.
#
# Usage:
#   make new-language ISO=ceb LANG_NAME=Cebuano
#   make update-language ISO=ceb CLEAN_OUT=1
#   make new-edition ISO=fra          # after adding the edition to config/language_editions.json
#   make update-batch SPEC=config/onboard_batch_id.json
#   make update-all
#   make new-catalog                  # non-DBT sweep of the full cross-source catalog
#   make new-catalog-dbt              # DBT-inclusive follow-up sweep
#   make status
#   make clean-out ISO=ceb            # or CLEAN_OUT_ALL=1 for every already-exported language
#   make publish ISO=ceb
#   make publish-span-profile
#   make publish-all

.ONESHELL:
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

PY := python3
LOAD_ENV = if [ -f .env ]; then export $$(grep -v '^\#' .env | xargs); fi

.PHONY: help new-language update-language new-edition update-edition update-batch update-all \
        new-batch new-catalog new-catalog-dbt status clean-out publish publish-span-profile publish-all \
        _require-iso _require-spec

help:
	@sed -n '2,33p' Makefile

_require-iso:
	@if [ -z "$${ISO:-}" ]; then echo "ISO is required, e.g. make $(MAKECMDGOALS) ISO=ceb" >&2; exit 1; fi

_require-spec:
	@if [ -z "$${SPEC:-}" ]; then echo "SPEC is required, e.g. make $(MAKECMDGOALS) SPEC=config/onboard_batch_id.json" >&2; exit 1; fi

# --- per-language: the full 9-step chain ---

new-language: _require-iso
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.full_chain --iso "$(ISO)" \
	  $(if $(LANG_NAME),--lang-name "$(LANG_NAME)") \
	  $(if $(filter 1,$(CLEAN_OUT)),--clean-out)

update-language: _require-iso
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.full_chain --iso "$(ISO)" \
	  $(if $(LANG_NAME),--lang-name "$(LANG_NAME)") \
	  $(if $(filter 1,$(CLEAN_OUT)),--clean-out)

# new-edition/update-edition are the SAME mechanism as new-language/update-language: editions_for()
# always re-derives the pool from config/language_editions.json (or catalog auto-discovery) on every
# call — there's no separate "just this one edition" code path. Add/change the edition in that config
# first (if it isn't already auto-discovered), then re-run the chain; the newly-added edition gets
# ingested and pooled in alongside whatever was already there.
new-edition: _require-iso
	@echo "[new-edition] if the edition isn't already auto-discovered from the catalog, add it to" >&2
	@echo "  config/language_editions.json for '$(ISO)' first — then this re-runs the full chain," >&2
	@echo "  which re-derives the pool from current config and ingests whatever's new." >&2
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.full_chain --iso "$(ISO)" $(if $(LANG_NAME),--lang-name "$(LANG_NAME)")

update-edition: new-edition

# --- batch: many languages at once ---

update-batch: _require-spec
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_batch --spec "$(SPEC)" --force --full

update-all:
	$(LOAD_ENV)
	$(PY) pipeline/scripts/update_all.py

new-batch: _require-spec
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_batch --spec "$(SPEC)" --full

new-catalog:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_catalog --full

new-catalog-dbt:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_catalog --include-dbt --full

# --- visibility ---

status:
	$(PY) pipeline/scripts/status.py

# --- out/ cleanup (opt-in, per-language or --all) ---

clean-out:
	@if [ "$${CLEAN_OUT_ALL:-0}" = "1" ]; then \
	  echo "[clean-out] removing ALL raw jsonl under pipeline/work/out/ — make sure every dataset that" >&2; \
	  echo "  needs it (lexeme-alignments, aligned_mwe, senses_attested, compact-alignments) has" >&2; \
	  echo "  already been exported for every language you care about." >&2; \
	  rm -f pipeline/work/out/align_*.jsonl; \
	elif [ -n "$${ISO:-}" ]; then \
	  $(PY) pipeline/scripts/clean_out.py --iso "$(ISO)"; \
	else \
	  echo "clean-out needs ISO=<iso> or CLEAN_OUT_ALL=1" >&2; exit 1; \
	fi

# --- publish (always separate, always manual) ---

publish: _require-iso
	$(LOAD_ENV)
	$(PY) pipeline/scripts/publish_lang.py --iso "$(ISO)" --create

publish-span-profile:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.cross_lang_prior --publish bcv-commons/cross-lingual-span-profile --create

publish-all:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.export_lex --publish-all bcv-commons/lexeme-alignments --create
	$(PY) -m lexeme_aligner.export_mwe --publish-all bcv-commons/aligned-mwe --create
	$(PY) -m lexeme_aligner.senses_attested --publish-all bcv-commons/senses-attested --create
	$(PY) -m lexeme_aligner.compact_align_batch --skip-generate --publish-hf bcv-commons/compact-alignments --create
	$(PY) -m lexeme_aligner.export_stopwords --publish bcv-commons/target-stopwords --create
	$(PY) -m lexeme_aligner.export_morph --publish bcv-commons/target-morphology --create
