# lexeme-aligner — high-level workflow commands.
#
# Wraps the underlying scripts (onboard.py, full_chain.py, onboard_batch.py, onboard_catalog.py,
# export_lex.py/export_mwe.py/senses_attested.py/export_stopwords.py/export_morph.py/
# compact_align_batch.py, cross_lang_prior.py) so the six everyday actions don't need their
# individual flags memorized. Finer-grained calls to those scripts directly are still there for the
# rare cases that need them — see advanced-docs/architecture.md.
#
# THE 9-STEP CHAIN (what "new-language"/"update-language" actually runs — see full_chain.py):
#   1 ingest  2 eflomal align  3 export(eflomal-only, LOCAL)  4 gloss (bootstraps from step 3)
#   5 gapfill  6 export(final union, LOCAL)  7 aligned_mwe(LOCAL)  8 senses_attested(LOCAL)
#   9 compact-alignments(LOCAL)
# Every step writes LOCAL files only (a few KB-MB each) — publishing to Hugging Face is ALWAYS a
# separate, deliberate step (the `publish`/`publish-all`/`publish-span-profile` targets below),
# decoupled on purpose so HF's 128-commits/hour/repo limit is never a per-language concern.
#
# `out/`'s raw per-verse jsonl is KEPT, gzip-compressed (~1/10th the size) — every chain target below
# compresses a language's own out/ jsonl automatically once ITS OWN chain finishes (steps 7/8/9 — aligned_mwe/
# senses_attested/compact-alignments — always run first). It used to be deleted; a retroactive fix across
# already-published languages then meant re-running eflomal+gloss for all of them. Readers are gzip-transparent
# (align_files.AlignPath). No flag needed.
#
# HF publish chunk size (files per commit) is ONE global setting, config.HF_CHUNK_SIZE, used by every
# publish/publish-all target — no per-script flag to remember. Override via ALIGNER_HF_CHUNK_SIZE (env
# or .env, picked up by $(LOAD_ENV) below) if a publish run starts hitting ReadTimeout/WriteTimeout;
# lower it (e.g. 100) rather than editing any script.
#
# Usage:
#   make new-language ISO=ceb LANG_NAME=Cebuano
#   make update-language ISO=ceb
#   make new-edition ISO=fra          # after adding the edition to config/language_editions.json
#   make update-batch SPEC=config/onboard_batch_id.json
#   make update-all                  # resumable: skips languages already done in the current sweep
#   make update-all FRESH=1           # ...start a genuinely new sweep instead of resuming
#   make new-catalog                  # non-DBT sweep of the full cross-source catalog
#   make new-catalog-dbt              # DBT-inclusive follow-up sweep
#   make status
#   make text-strip-report            # bracket/paren clues -> config/text_strip_report.md (see it + config/text_strip_rules.json)
#   make clean-out ISO=ceb            # catch-up cleanup for out/ jsonl that predates auto-clean-out
#                                      # (or CLEAN_OUT_ALL=1 — multi-dataset-safety-checked, not blind rm)
#   make publish ISO=ceb [PUSH=1]     # safe partial publish, dry run unless PUSH=1
#   make publish-ready                # which languages are safe to publish now
#   make publish-span-profile
#   make publish-all [PUSH=1] [INCLUDE_MWE=1]
#   make llm-align ISO=hinirv PUBLISH_ISO=hin USJ_DIR=pipeline/work/ingest-cache/usj-hinirv PROVIDER=mock   # opt-in LLM experiment
#   make llm-score ISO=hinirv PUBLISH_ISO=hin OUT_TAG=hinirv.gap-seeded.mock GOLD_ISO=hin

.ONESHELL:
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# the project's venv when it exists (the system python3 has neither lexeme_aligner nor pyarrow); falls back to python3
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
LOAD_ENV = if [ -f .env ]; then export $$(grep -v '^\#' .env | xargs); fi

.PHONY: llm-align llm-score help new-language update-language new-edition update-edition update-batch update-all \
        new-batch new-catalog new-catalog-dbt status text-strip-report clean-out publish \
        publish-span-profile publish-senses-ubs publish-all _require-iso _require-spec

help:
	@sed -n '2,46p' Makefile

_require-iso:
	@if [ -z "$${ISO:-}" ]; then echo "ISO is required, e.g. make $(MAKECMDGOALS) ISO=ceb" >&2; exit 1; fi

_require-spec:
	@if [ -z "$${SPEC:-}" ]; then echo "SPEC is required, e.g. make $(MAKECMDGOALS) SPEC=config/onboard_batch_id.json" >&2; exit 1; fi

# --- per-language: the full 9-step chain ---

new-language: _require-iso
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner run "$(ISO)" \
	  $(if $(LANG_NAME),--lang-name "$(LANG_NAME)") \
	  $(if $(filter 1,$(SKIP_INGEST)),--skip-ingest)

# SKIP_INGEST=1 re-runs the chain against already-cached text (pipeline/work/ingest-cache/usj-<tag>)
# with NO network fetch at all — the normal way to re-process a language after an algorithm fix
# (e.g. the gloss bootstrap-iso bug) without re-downloading anything.
update-language: _require-iso
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner run "$(ISO)" \
	  $(if $(LANG_NAME),--lang-name "$(LANG_NAME)") \
	  $(if $(filter 1,$(SKIP_INGEST)),--skip-ingest)

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
	$(PY) -m lexeme_aligner run "$(ISO)" $(if $(LANG_NAME),--lang-name "$(LANG_NAME)")

update-edition: new-edition

# --- batch: many languages at once ---

# SKIP_INGEST=1 re-runs against already-cached text only — no network fetch at all. out/ cleanup is
# always on (each language cleans its own raw jsonl right after ITS OWN chain finishes).
update-batch: _require-spec
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_batch --spec "$(SPEC)" --force --full --clean-out \
	  $(if $(filter 1,$(SKIP_INGEST)),--skip-ingest)

update-all:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner batch --all $(if $(filter 1,$(SKIP_INGEST)),--skip-ingest) $(if $(filter 1,$(FRESH)),--fresh) $(if $(MIN_FREE_GB),--min-free-gb $(MIN_FREE_GB))

new-batch: _require-spec
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_batch --spec "$(SPEC)" --full --clean-out

new-catalog:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_catalog --full

new-catalog-dbt:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.onboard_catalog --include-dbt --full

# --- visibility ---

status:
	$(PY) pipeline/scripts/status.py

# Clues (not decisions) for config/text_strip_rules.json — see that file's _doc. Writes
# config/text_strip_report.md; pass MIN_PCT=0.05 etc. to override the inclusion threshold.
text-strip-report:
	$(PY) pipeline/scripts/text_strip_candidates.py $(if $(MIN_PCT),--min-pct $(MIN_PCT))

# --- out/ cleanup (opt-in, per-language or --all) ---

clean-out:
	@if [ "$${CLEAN_OUT_ALL:-0}" = "1" ]; then \
	  $(PY) pipeline/scripts/clean_out_safe.py --delete; \
	elif [ -n "$${ISO:-}" ]; then \
	  $(PY) pipeline/scripts/clean_out.py --iso "$(ISO)"; \
	else \
	  echo "clean-out needs ISO=<iso> or CLEAN_OUT_ALL=1" >&2; exit 1; \
	fi

# --- publish (always separate, always manual) ---

# Every publish below goes through pipeline/scripts/publish_safe.py (merges only the selected languages into the manifest that is on
# Hugging Face now, never deletes, preflight + schema guard + verify; the compact sidecar layers go to their own repos). DRY RUN unless PUSH=1.
publish: _require-iso
	$(LOAD_ENV)
	$(PY) pipeline/scripts/publish_lang.py --iso "$(ISO)" $(if $(PUSH),--push)

# which languages are safe to publish right now, and why the others are not (writes pipeline/work/logs/publish_ready_latest.json)
publish-ready:
	$(LOAD_ENV)
	$(PY) pipeline/scripts/publish_safe.py --list-ready

publish-span-profile:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.cross_lang_prior --publish bcv-commons/cross-lingual-span-profile --create

# The UBS-sense-keyed attestation is a SEPARATE dataset with its own license (CC BY-SA 4.0, UBS Dictionary of Biblical
# Hebrew), deliberately NOT part of publish-all: publishing it is its own decision.
publish-senses-ubs:
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.senses_attested --scheme ubs --publish-all bcv-commons/senses-attested --create

# cross-lingual-span-profile is now sourced from lexeme-alignments+aligned_mwe (both persisted local
# datasets, not transient out/), so it's no longer timing-sensitive — safe to fold in as the last step.
# publish-all = every language that is ready right now (publish_safe) — a dry run unless PUSH=1; INCLUDE_MWE=1 adds aligned_mwe (its schema
# guard refuses a partial publish, so that one goes out for all languages at once). The small whole-tree datasets (stopwords,
# morphology, span profile) are a separate `make publish-extras`.
publish-all: publish-ready
	$(LOAD_ENV)
	$(PY) pipeline/scripts/publish_safe.py --ready-file pipeline/work/logs/publish_ready_latest.json $(if $(PUSH),--push) $(if $(INCLUDE_MWE),--include-mwe)
	@echo "(the stopwords / morphology / span-profile datasets are separate: make publish-extras)"

# real publishes of the small whole-tree datasets. NOTE: never put $(MAKE) in these recipes — `make -n` EXECUTES recipes that contain it, and with
# .ONESHELL that runs the whole recipe for real (a "dry" check of a publish target would publish).
publish-extras: publish-span-profile
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.export_stopwords --publish bcv-commons/target-stopwords --create
	$(PY) -m lexeme_aligner.export_morph --publish bcv-commons/target-morphology --create

# --- opt-in LLM-alignment experiment (internal-docs/llm-align-experiment-plan.md, advanced-docs/llm-experiment.md) ---
# NEVER part of the default chain. Everything but PROVIDER=mock spends money (API key) or subscription quota
# (PROVIDER=cli) — start with `EXTRA=--dry-run`, which prints the prompt and an estimate and calls nothing.
#   make llm-align ISO=fra-lsg PUBLISH_ISO=fra USJ_DIR=pipeline/work/ingest-cache/usj-fra-lsg STRATEGY=gap-seeded \
#        PROVIDER=anthropic MODEL=claude-sonnet-5 EXTRA="--nt --batch --max-usd 10"
llm-align: _require-iso
	$(LOAD_ENV)
	$(PY) -m lexeme_aligner.eval.llm_align --iso "$(ISO)" --publish-iso "$(or $(PUBLISH_ISO),$(ISO))" \
	  --usj-dir "$(USJ_DIR)" --strategy "$(or $(STRATEGY),gap-seeded)" --provider "$(or $(PROVIDER),anthropic)" \
	  --model "$(or $(MODEL),claude-sonnet-5)" $(if $(BOOKS),$(foreach b,$(BOOKS),--book $(b)),$(if $(findstring --nt,$(EXTRA))$(findstring --ot,$(EXTRA))$(findstring --all,$(EXTRA)),,--nt)) $(EXTRA)

llm-score: _require-iso
	$(LOAD_ENV)
	@if [ -z "$${OUT_TAG:-}" ]; then echo "OUT_TAG is required, e.g. make llm-score ISO=hinirv OUT_TAG=hinirv.gap-seeded.sonnet5 GOLD_ISO=hin" >&2; exit 1; fi
	$(PY) -m lexeme_aligner.eval.llm_align --report --iso "$(ISO)" --publish-iso "$(or $(PUBLISH_ISO),$(ISO))" \
	  --out-tag "$(OUT_TAG)" --gold-iso "$(or $(GOLD_ISO),$(or $(PUBLISH_ISO),$(ISO)))"
