# Bibles — the recipe layer (source-of-truth ingest for the aligner)

How `bcv-commons/bibles` and the aligner get target-language Bible text **without caching it** —
so there is one source of truth (upstream), reproducible builds, and no silent drift. Companion to
`docs/aligner-plan.md` (which covers the alignment itself + the USJ/Burrito input decision).

## Principle: single source of truth

The actual Bible text **stays at its origin** (helloAO's CDN, `cdn.bibel.wiki` PKF, eBible, …).
`bcv-commons/bibles` is **not a copy of the texts** — copying invites drift (upstream fixes a typo,
our cache is now wrong and silently diverges). Instead it is a thin **recipe layer**:

> `bcv-commons/bibles` holds, per text: a **pointer** to the upstream source, a **pin** (version +
> `sha256` of exactly what we consumed), the **metadata** (iso639-3, license, attribution,
> **versification**), and the **conversion recipe** (the scripts + fixes that turn that source into
> our canonical **USJ**). It does **not** hold the text.

This is npm-lockfile thinking for Bibles: the registry (upstream) is the truth; we pin a version +
hash and carry the build recipe. On rebuild we re-fetch and **verify the hash** — a mismatch means
upstream drifted, so we re-pin *deliberately* and re-run the alignment. Never a stale cache.

## The ingest pipeline

```
upstream source (pinned)          conversion recipe (bcv-commons/bibles)        canonical
──────────────────────            ──────────────────────────────────────       ─────────
helloAO JSON  ───────────────►  helloao_json → USJ           (Python)      ┐
cdn.bibel.wiki  <coll>.pkf ──►  pkf → USFM (Node edge) → USJ (usfmtc,Py)   ┼──►  USJ  ──►  aligner
eBible / DBL   USFM/USX ─────►  usfmtc  USFM/USX → USJ        (Python)      ┘        │
                                 + heading-quarantine + versification map            ▼
                                                                        (optional) Burrito snapshot
                                                                        → zip → CDN / GitHub Releases
```

- **Canonical internal format: USJ** (Python-native via `usfmtc`; a first-class Scripture Burrito
  content format). Everything downstream of USJ is source-, format-, language-agnostic.
- **The recipe (scripts + pins + metadata) is what lives in git.** Texts and heavy artifacts do not.

## Sources & their converters

| Source | Publishes | Converter | Runs |
|---|---|---|---|
| **helloAO** (helloao.org) | rendered JSON **+ USFM** | helloAO-JSON→USJ, or its USFM via `usfmtc` | Python |
| **cdn.bibel.wiki** | **PKF** (Proskomma Kit Format, binary) | `.pkf → USFM` (Node), then `usfmtc` → USJ | **Node (once, at the edge)** + Python |
| **eBible / DBL / Door43** | USFM / USX | `usfmtc` → USJ | Python |

**Prefer the no-Node path.** If a text is available as helloAO JSON or plain USFM, use that (pure
Python). Reach for the Node `.pkf` converter only for texts that are **PKF-only** (e.g. `cdn.bibel.wiki`
languages not in helloAO).

## The PKF edge converter (the one Node dependency)

The blocker: `cdn.bibel.wiki` publishes **PKF** (a compressed Proskomma succinct docSet), and the only
thing that reads PKF is **Proskomma (JS)**. Resolution: run Proskomma **once, as a build-time
converter at the ingestion edge** — never a runtime/server dependency. `.pkf → USFM` in Node, then the
rest of the pipeline is pure Python (`usfmtc` USFM→USJ). Confine Node to this one CLI/container.

- Current tool: **`example/scripts/export_usfm.mjs <iso>`** — reads `pipeline/work/ingest-cache/pkf-pool/<iso>/*.pkf`
  (proskomma-core + fflate), writes `temp/usfm-<iso>/<NN>-<BOOK>.usfm` via proskomma's native `usfm`
  document field. Isolate it as a container/`npx` step invoked from Python `subprocess`; its output
  (USFM → USJ) is what we pin + hash.

### `cdn.bibel.wiki` PKF layout
Root: `https://cdn.bibel.wiki/pkf/`

| Path | What |
|---|---|
| `pkf/manifest.json` | index: `{updated_at, languages:[{iso, version, pkfs:[…], catalogs:[…], pkf_bytes}]}` (589 languages) |
| `pkf/<iso>/<collection>.<hash>.pkf` | the PKF binary, e.g. `pkf/ind/ind_C01.CN8xM8h_.pkf` |
| `pkf/<iso>/<collection>.<hash>.json` | companion catalog |
| `pkf/<iso>/app-config.json` | per-language UI config (not needed for ingest) |
| `pkf/_app/nav-base.json` | shared UI strings (not needed) |

The hashed filename in `manifest.json` **is** a natural pin — record `{iso, pkf: "ind_C01.CN8xM8h_.pkf", pkf_bytes, manifest updated_at, sha256}`.

## Reproducibility & publishing

- **Pin per text:** upstream URL + version/hash + `sha256` of the consumed bytes. Re-fetch + verify on
  build; mismatch ⇒ deliberate re-pin.
- **Link-rot insurance (optional):** publish the *derived* USJ as a **Scripture Burrito** snapshot
  (USJ ingredient + metadata incl. versification), **zip → CDN / GitHub Releases** — a frozen,
  reproducible artifact *downstream* of the pin, never a competing source of truth. Same pattern the
  repo already uses (TSV committed, `.db`/bulk rebuilt-or-released, not cached in git).

## Versification & headings

Handled in the converter, per `docs/aligner-plan.md` §"Versification & the heading trap":
- **helloAO** normalizes to Protestant versification and **embeds psalm superscriptions inconsistently**
  (English separates as `hebrew_subtitle`; German folds into v1) — prefer its **USFM** (`\d` title,
  cleanly separable), and record `versification=protestant`.
- The spine is **Hebrew-numbered**, so Psalms need the **Protestant↔Hebrew `vrs` map (V1)** on the
  target→spine hop; the first experiment skips Psalms until V1.
- The manifest/metadata's **`versification` field** ships *inside* the recipe/Burrito, so the aligner
  always knows it (closing the gap a bare API/URL leaves open).

## Prototype → production (validated 2026-07 on Indonesian)

> **License note (pilot text):** the Indonesian source (`cdn.bibel.wiki` `ind_C01`) is
> **Public Domain** — verbally confirmed 2026-07; written confirmation pending. Record it in the
> per-text pin/metadata once the written form arrives. Treated as experiment-only until then.


The temporary run below **is** the recipe layer in miniature — the same four moves `bcv-commons/bibles`
will make. It was validated end-to-end (`cdn.bibel.wiki` PKF → 67 USFM books → 67 USJ books), so the
concrete facts here are what to carry forward.

### What we ran (and it worked)
```bash
# deps: Node v22 + proskomma-core + fflate  (npm, 259 pkgs);  Python + usfmtc (already in-repo)
curl -s https://cdn.bibel.wiki/pkf/manifest.json           # index → find the language's .pkf
curl -sL -o pipeline/work/ingest-cache/pkf-pool/ind/ind_C01.CN8xM8h_.pkf \          # download the pinned PKF (3.3 MB)
     https://cdn.bibel.wiki/pkf/ind/ind_C01.CN8xM8h_.pkf
node example/scripts/export_usfm.mjs ind                   # PKF → temp/usfm-ind/<NN>-<BOOK>.usfm (67)
# Python: for each usfm →  usfmtc.readFile(f).outUsj()  → temp/usj-ind/*.json  (USJ 3.0)
```

### Facts to reuse
- **`manifest.json` shape:** `{updated_at, languages:[{iso, version, pkfs:[<coll>.<hash>.pkf],
  catalogs:[…], pkf_bytes, styles, fonts}]}` — 589 languages. The **hashed pkf filename is a natural
  version pin** (`ind_C01.CN8xM8h_.pkf`); record it + a `sha256` of the downloaded bytes.
- **PKF read (Node):** `proskomma-core` + `fflate` — `loadSuccinctDocSet(JSON.parse(strFromU8(decompressSync(bytes))))`,
  then GraphQL `document(bookCode){usfm}` per book. proskomma's **native `usfm` field** does the export —
  no separate tool. (`example/scripts/export_usfm.mjs` is the working reference.)
- **USFM→USJ (Python):** `doc = usfmtc.readFile(path); usj = doc.outUsj()` → `{type:"USJ", version:"3.0",
  content:[…]}`. `usfmtc` also gives `outUsx()`, `fromUsj()` (round-trip), `saveAs()`.
- **USJ structure confirmed** (Matthew): element `type`s = `book, chapter, para, verse, note, char`.
  **Apparatus separates by type** — exclude `note` (footnotes) + heading `para` (`s*`) + title `para` (`d`),
  keep verse text + `char` (`w`). The adapter's type-based exclusion works on real data.

### Temporary step → production component
| Temporary (now) | Production (`bcv-commons/bibles` recipe layer) |
|---|---|
| manually read `manifest.json`, pick the `.pkf` | **pinned fetcher** — resolve iso → the manifest entry; store the pin `{iso, pkf, pkf_bytes, manifest updated_at, sha256}` in the recipe |
| `curl` PKF → `pipeline/work/ingest-cache/pkf-pool/<iso>/` | fetch **by pin**, **verify `sha256`** (drift check → deliberate re-pin) |
| `node export_usfm.mjs <iso>` (local `node_modules`) | the same script as a **containerized / `npx` one-shot edge converter**, invoked from Python `subprocess`; pin `proskomma-core`+`fflate` versions |
| `usfmtc.readFile().outUsj()` | the shared **Python format layer** (USFM/USX → USJ) — reused for helloAO-USFM + eBible too |
| `temp/usj-ind/*.json` | the aligner input, and/or a published **Burrito snapshot** (USJ ingredient + metadata) |

### Porting checklist
- [ ] Lift `example/scripts/export_usfm.mjs` into `bcv-commons/bibles` as the **PKF→USFM** converter;
      commit a `package.json`/lockfile pinning `proskomma-core` + `fflate`; wrap it as a container so a
      Python dev never runs npm.
- [ ] Behind a **Python `subprocess`** call, so the pipeline stays Python end-to-end (Node only at the edge).
- [ ] Record the **pin** from `manifest.json` (hashed filename = version) + a `sha256` of the bytes.
- [ ] Reuse `usfmtc … .outUsj()` as the **one shared format layer** for every USFM/USX source.
- [ ] Attach the **`versification`** field to the metadata (from the source/catalog, or assigned —
      helloAO = `protestant`); apply the Protestant↔Hebrew map on the spine hop (Psalms).
- [ ] Optionally emit a **Scripture Burrito** (USJ ingredient + metadata) and publish zip → CDN / GitHub
      Releases for a reproducible, link-rot-proof snapshot.
