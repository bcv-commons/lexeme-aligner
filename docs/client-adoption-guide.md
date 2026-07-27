# Client adoption guide — the new CDN data structure

**Audience:** client apps consuming the data (`se-regional-pwa` and any other reader/picker).
**Status:** the *text* side (manifest + catalogs) is **live now**; the *audio/timing* side (`/dbt/`) and the
unified picker index (`availability.json`) are **coming** (DBT redesign in progress). Adopt the live parts now
and code defensively for the rest.
**Companions:** `cdn-delivery-contract.md` (the shapes + Appendix A codec), `docs/cdn-data-delivery-spec.md`
(your original wishlist — this is its answer).

---

## 0. What's changing, in three sentences

1. **One origin** — everything you fetch now comes from `cdn.bibel.wiki` (text under `/pkf/`, audio/timing
   under `/dbt/`, the picker index at `/_app/`). No more build-time GitHub artifacts (`ALL-langs-*`,
   `templates/*/ALL-timings`).
2. **Thin + compact for discovery, heavy on demand** — the picker runs off one small index using compact
   string codes; per-language detail (filesets, per-book coverage) is fetched only when a language is opened.
3. **Two tiny codes to learn** — `media` and `codex` (decode with `.includes()`); they replace the old
   audio/video/timing booleans and testament lists everywhere.

---

## 1. What's live now vs coming (adopt accordingly)

| Endpoint | Purpose | Status |
|---|---|---|
| `GET /pkf/manifest.json` | discovery index (iso-keyed, compact `media`/`codex`/`collections`) | ✅ **live** (589 langs) |
| `GET /pkf/<iso>/<catalog>.json` | exact per-book coverage (`documents[].bookCode`) | ✅ live |
| `GET /pkf/<iso>/app-config.json` | name, collection, copyright, themes | ✅ live |
| `GET /pkf/<iso>/<name>.<hash>.pkf` | the scripture text (Proskomma docset) | ✅ live |
| `GET /dbt/<iso>/audio.json` | per-canon audio/timing + filesets + `sources` | 🚧 **coming** |
| `GET /dbt/<iso>/timing/<BOOK>.json` | verse timecodes (`audioFileset→ch→verse→[s,e]`) | 🚧 coming |
| `GET /_app/availability.json` | **the picker index** — text ⋈ audio, one fetch | 🚧 coming |

**Practical takeaway:** build your picker against `manifest.json` **now** (it already carries `media`/`codex`
so you can badge audio/text/timing for the 589 PKF languages). Swap the picker to `availability.json` when it
lands (it adds names, `audioSource`, and the ~1,400 non-PKF audio languages in one fetch).

---

## 2. The fetch plan (hybrid — one index, detail on demand)

```
App boot / language picker:
  → availability.json   (coming; one thin fetch, all offerable langs)   ── OR ──
  → manifest.json       (live now; PKF langs, compact codes)
      Filter/badge entirely from the compact codes. Do NOT fan out per-language here.

User opens language <iso>:
  text:   /pkf/<iso>/app-config.json  + /pkf/<iso>/<catalog>.json   (name, books)
          /pkf/<iso>/<name>.<hash>.pkf                              (the text)
  audio:  /dbt/<iso>/audio.json                                     (filesets, sources)   (coming)
  timing: /dbt/<iso>/timing/<BOOK>.json                            (per opened book)      (coming)
```

The rule: **the index is for the picker; per-language files are for the reader.** Never fetch hundreds of
per-language files to build a picker — that's exactly what the compact index exists to avoid.

---

## 3. The one thing every client must implement — the `media` / `codex` codec

Two compact strings appear at every layer (manifest, `audio.json`, `availability.json`). **Decode with
`.includes()`, never `===`** — so an unknown future letter degrades gracefully instead of breaking.

### `media` — audio / video / timing presence
Letters in fixed order `a,v,t,s`: `a`=audio, `v`=video, `t`=audio-timecode, `s`=video-sync. `""` = none.
Invariants `t⇒a`, `s⇒v`. Valid: `"" a v av at vs avt avs avts`.

### `codex` — testaments present (with text)
Letters in canon order `o,n,d`: `o`=OT, `n`=NT, `d`=DC. `""` = **no text** (a media-only language). Valid:
`"" o n d on od nd ond`.

```js
// media
const hasAudio  = m => m.includes('a');
const hasVideo  = m => m.includes('v');
const audioSync = m => m.includes('t');   // karaoke-style verse highlight for audio
const videoSync = m => m.includes('s');   // future
const hasMedia  = m => m !== '';

// codex (text coverage)
const hasOT = c => c.includes('o');
const hasNT = c => c.includes('n');
const hasText = c => c !== '';             // '' => media-only, no scripture text

// badges for a language entry
const badges = lang => [
  hasText(lang.codex) && '📖',
  hasAudio(lang.media) && '🔊',
  audioSync(lang.media) && '⏱',
  hasVideo(lang.media) && '🎬',
].filter(Boolean);
```

Full tables + encoder + invariants: `cdn-delivery-contract.md` **Appendix A**.

---

## 4. Behavioural rules that will bite if ignored

- **Media-only languages exist.** Some entries have `codex: ""` and `collections: []` — audio/video but **no
  scripture text** (4 today: `jam`, `tcf`, `tpl`, `tpx`; more coming as non-PKF audio langs land). **Don't
  assume every language has text.** Offer the audio/story experience; don't open a text reader.
- **Not all audio plays without a key.** `audioSource` (in `availability.json`) tells you the best source:
  - `cdn` / `helloao` / `contrib` — **keyless**, play for everyone.
  - `dbt` — **key-gated**: needs *your deployment's* `DBT_API_KEY` via the `dbt-proxy`. A `dbt`-only language
    won't play without it. Badge/offer keyless audio unconditionally; treat `dbt`-only as conditional.
- **Prefer `.includes()` and ignore unknown keys.** New `media` letters (`s`) and new JSON fields will appear
  additively. Equality checks (`media === 'avt'`) and sealed parsers will break on the next enrichment.
- **Caching:** content-hashed files (`.pkf`, catalog) are immutable — cache forever. Index/config files
  (`manifest.json`, `availability.json`, `audio.json`, `app-config.json`) are `max-age=300`; timing
  `max-age=3600`. Use `updated_at` on each index for cheap staleness checks.
- **Multi-collection languages** (e.g. `niy` = French + Ndruna) list >1 entry in `collections[]`; the
  top-level `media`/`codex` is the **union**. For the picker, the union is what you badge; for the reader, let
  the user pick the collection.

---

## 5. Migration map — where each thing moves (off GitHub, onto the CDN)

| You used to read… | Now read… |
|---|---|
| `ALL-langs-compact.json` (GitHub) — names | `availability.json` (names folded in) |
| `ALL-langs-data/**` (GitHub) — audio/timing flags + filesets | `availability.json` (flags) + `/dbt/<iso>/audio.json` (filesets) |
| 588 × `info.json` fan-out — media presence | one `availability.json` fetch (or `manifest.json` `media` codes) |
| `templates/*/ALL-timings/**` (GitHub) — verse timing | `/dbt/<iso>/timing/<BOOK>.json` (template dimension folded away) |
| old `manifest.json` array (`version`/`styles`/`fonts`) | new iso-keyed `manifest.json` with `media`/`codex`/`collections` |
| per-canon `audio`/`video`/`timing` booleans | the `media` code |
| `books:{count,testaments}` / testament lists | `books` int + `codex` code |

---

## 6. Still being finalized — code defensively here

- **`/dbt/` tree + `availability.json`** — shapes are locked (see the contract + schemas) but not published
  yet. Build against the schemas; feature-detect (404 → fall back to today's source) until live.
- **`textSource`** — `availability.json` currently signals PKF text via `pkf: true`, but a language may have
  **helloAO** text and no PKF. A per-source text indicator is still being designed; for now treat `pkf` as
  "PKF reader available" and expect an additive `textSource` field later (ignore it safely until you use it).
- **Folder name** — audio/timing is under `/dbt/` (may be renamed `/audio/`); don't hardcode the base in many
  places — one constant.

---

## 7. Adopt-now checklist

- [ ] Implement the `media`/`codex` codec (§3) with `.includes()` — this unblocks everything.
- [ ] Point the picker at `manifest.json` now; badge text/audio/timing from the codes.
- [ ] Handle **media-only** languages (`codex:""`) — no text reader.
- [ ] Treat `dbt` audio as key-gated; offer keyless (`cdn`/`helloao`/`contrib`) unconditionally.
- [ ] Cache by the rules in §4; read `updated_at` for staleness.
- [ ] Feature-detect `/dbt/**` + `availability.json`; fall back to current sources until they're live.
- [ ] Parse forward-compatibly (ignore unknown keys; no `===` on codes).
