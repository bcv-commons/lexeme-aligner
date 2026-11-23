// part-alignments -> compact-alignments: the fold library (JavaScript; pa_fold.py is the same in Python, see its docstring
// for the rules). No dependencies. Published with bcv-commons/part-alignments as fold.mjs.
//
//   import { foldVerse, foldBook, compactIndex } from "./fold.mjs";
//   const index = await (await fetch(".../_index/MRK_parts.json")).json();
//   const [main, meta, extra] = foldBook(index, partMain, partMeta, partExtra);   // compact's three arrays
//
//   foldBook(index, partMain, partMeta, partExtra, "stem")                          // fold rule "stem": carrier unit only
//
// CLI (Node): node fold.mjs --parts DIR --out DIR [--index-only] [--fold-rule fold|stem]

export const CHANNELS = ["method", "conf", "contested", "bonus", "rule", "rend", "parts", "runs"];
const DENSE = ["method", "conf", "rend", "runs"];
const SPARSE = ["contested", "bonus", "rule", "parts"];
const NONE = "_";
export const FOLD_RULES = ["fold", "stem"];

export function positions(span) {
  const out = [];
  for (const piece of span.split(",")) {
    const [a, b] = piece.split("-");
    const lo = parseInt(a, 10), hi = b === undefined ? lo : parseInt(b, 10);
    for (let i = lo; i <= hi; i++) out.push(i);
  }
  return out;
}

export function encodeSpan(pos) {
  if (pos.length === 1) return String(pos[0]);
  const lo = Math.min(...pos), hi = Math.max(...pos);
  if (hi - lo + 1 === pos.length) return `${lo}-${hi}`;
  return [...pos].sort((x, y) => x - y).join(",");
}

const words = (s) => s.split(/\s+/).filter(Boolean);

export function entries(s) {
  const out = new Map();
  for (const e of words(s)) {
    const i = e.indexOf(":");
    out.set(parseInt(e.slice(0, i), 10), e.slice(i + 1));
  }
  return out;
}

function sparse(s) {
  const out = new Map();
  for (const e of words(s)) {
    const i = e.indexOf(":");
    const o = parseInt(e.slice(0, i), 10);
    if (!out.has(o)) out.set(o, []);
    out.get(o).push(e.slice(i + 1));
  }
  return out;
}

function partKey(x) {
  const n = x.split(".").map((v) => parseInt(v, 10));
  return x.includes(".") ? n : [n[0], -1];
}

function cmpKey(a, b) {
  const ka = partKey(a), kb = partKey(b);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const d = (ka[i] ?? -Infinity) - (kb[i] ?? -Infinity);
    if (d) return d;
  }
  return 0;
}

export function foldVerse(verse, main, meta = {}, extra = "", foldRule = "fold") {
  if (!FOLD_RULES.includes(foldRule)) throw new Error(`foldRule must be one of ${FOLD_RULES}, not ${foldRule}`);
  const { units, tokens } = verse;
  const all = entries(main);
  const links = new Map([...all].filter(([, sp]) => sp !== NONE));
  const linkedOrder = [...all].filter(([, sp]) => sp !== NONE).map(([o]) => o).sort((a, b) => a - b);
  const denseAt = new Map(linkedOrder.map((o, i) => [o, i]));
  const dense = {};
  for (const c of DENSE) if (c in meta) dense[c] = c === "rend" ? words(meta[c]) : [...meta[c]];
  const sp = {};
  for (const c of SPARSE) if (c in meta) sp[c] = sparse(meta[c]);
  const xl = entries(extra);
  const members = new Map();
  units.forEach((u, o) => {
    if (u[3] >= 0) {
      if (!members.has(u[3])) members.set(u[3], []);
      members.get(u[3]).push(o);
    }
  });
  const owner = new Map();
  tokens.forEach((t, k) => {
    if (links.has(t[3])) for (const p of positions(links.get(t[3]))) {
      if (!owner.has(p)) owner.set(p, new Set());
      owner.get(p).add(k);
    }
  });
  const claimedByOther = (p, k) => { const s = owner.get(p); return !!s && [...s].some((x) => x !== k); };

  const out = [], outX = [];
  const ch = Object.fromEntries(CHANNELS.map((c) => [c, []]));
  let cord = -1;
  tokens.forEach((t, k) => {
    if (!t[2]) return;
    cord += 1;
    const c = t[3];
    const unitsK = foldRule === "stem" ? [c] : [c, ...(members.get(k) || []).filter((o) => o !== c)];
    const got = [], givers = [];
    for (const o of unitsK) {
      if (!links.has(o)) continue;
      let pos = positions(links.get(o));
      if (o !== c) pos = pos.filter((p) => !claimedByOther(p, k));
      const neu = pos.filter((p) => !got.includes(p));
      if (neu.length || (o === c && pos.length)) givers.push(o);
      got.push(...neu);
    }
    if (got.length) {
      const src = givers[0];
      const carrierSet = links.has(c) ? new Set(positions(links.get(c))) : null;
      const only = givers.length === 1 && givers[0] === c && carrierSet.size === new Set(got).size && got.every((p) => carrierSet.has(p));
      out.push(`${cord}:${only ? links.get(c) : encodeSpan(got)}`);
      for (const [d, vals] of Object.entries(dense)) {
        const i = denseAt.get(src);
        if (i !== undefined && i < vals.length) ch[d].push(vals[i]);
      }
      for (const name of ["contested", "rule"]) for (const rest of (sp[name]?.get(src) || [])) ch[name].push(`${cord}:${rest}`);
      if (sp.parts) {
        const tp = new Set();
        for (const o of givers) for (const rest of (sp.parts.get(o) || [])) for (const x of rest.split(",")) tp.add(x);
        if (tp.size) ch.parts.push(`${cord}:` + [...tp].sort(cmpKey).join(","));
      }
    } else {
      for (const rest of (sp.bonus?.get(c) || [])) ch.bonus.push(`${cord}:${rest}`);
      const xp = [];
      for (const o of unitsK) if (xl.has(o) && xl.get(o) !== NONE) for (const p of positions(xl.get(o))) if (!xp.includes(p)) xp.push(p);
      if (xp.length) {
        const cs = xl.has(c) ? new Set(positions(xl.get(c))) : null;
        const only = cs && cs.size === new Set(xp).size && xp.every((p) => cs.has(p));
        outX.push(`${cord}:${only ? xl.get(c) : encodeSpan(xp)}`);
      }
    }
  });
  const sep = { method: "", conf: "", rend: " ", runs: "" };
  const m = {};
  for (const c of CHANNELS) if (c in meta) m[c] = ch[c].join(sep[c] ?? " ");
  return [out.join(" "), m, outX.join(" ")];
}

export function foldBook(index, main, meta = {}, extra = null, foldRule = "fold") {
  const refs = Object.keys(index);
  const cm = [], cx = [], cmeta = {};
  for (const c of CHANNELS) if (c in meta) cmeta[c] = [];
  refs.forEach((ref, i) => {
    const vm = {};
    for (const c of Object.keys(meta)) vm[c] = meta[c][i];
    const [m, md, x] = foldVerse(index[ref], main[i], vm, extra ? extra[i] : "", foldRule);
    cm.push(m);
    cx.push(x);
    for (const c of Object.keys(cmeta)) cmeta[c].push(md[c] ?? "");
  });
  return [cm, cmeta, cx];
}

export function compactIndex(index) {
  const out = { lexemes: {}, keys: {}, fn: {}, fn_keys: {} };
  for (const [ref, v] of Object.entries(index)) {
    const toks = v.tokens;
    out.lexemes[ref] = toks.filter((t) => t[2]).map((t) => t[1]);
    out.keys[ref] = toks.filter((t) => t[2]).map((t) => t[0]);
    out.fn[ref] = toks.filter((t) => !t[2]).map((t) => t[1]);
    out.fn_keys[ref] = toks.filter((t) => !t[2]).map((t) => t[0]);
  }
  return out;
}

// ---- CLI (Node only) ---------------------------------------------------------------------------------------------------------
async function cli(argv) {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const arg = (n) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : null; };
  const parts = arg("--parts"), outRoot = arg("--out"), foldRule = arg("--fold-rule") || "fold";
  const load = (fp) => JSON.parse(fs.readFileSync(fp, "utf8"));
  // Python's json.dumps(ensure_ascii=False) default separators: ", " and ": "
  const dump = (fp, data) => {
    fs.mkdirSync(path.dirname(fp), { recursive: true });
    fs.writeFileSync(fp, pyJson(data) + "\n");
  };
  if (argv.includes("--index-only")) {
    for (const f of fs.readdirSync(path.join(parts, "_index")).filter((f) => f.endsWith("_parts.json")).sort()) {
      const book = f.split("_")[0];
      for (const [name, data] of Object.entries(compactIndex(load(path.join(parts, "_index", f)))))
        dump(path.join(outRoot, "_index", `${book}_${name}.json`), data);
    }
    return;
  }
  const eds = [];
  for (const a of fs.readdirSync(parts)) {
    if (a.startsWith("_") || !fs.statSync(path.join(parts, a)).isDirectory()) continue;
    for (const iso of fs.readdirSync(path.join(parts, a))) for (const ed of fs.readdirSync(path.join(parts, a, iso)))
      eds.push(path.join(a, iso, ed));
  }
  for (const rel of eds.sort()) {
    const dir = path.join(parts, rel);
    for (const name of fs.readdirSync(dir).sort()) {
      if (!name.endsWith(".json") || name.endsWith(".meta.json") || name.endsWith(".extra.json") || name.startsWith("_")) continue;
      const book = name.split("_")[0], stem = name.slice(0, -5);
      const index = load(path.join(parts, "_index", `${book}_parts.json`));
      const mfp = path.join(dir, stem + ".meta.json"), xfp = path.join(dir, stem + ".extra.json");
      const meta = fs.existsSync(mfp) ? load(mfp) : {};
      const extra = fs.existsSync(xfp) ? load(xfp) : null;
      const [cm, cmeta, cx] = foldBook(index, load(path.join(dir, name)), meta, extra, foldRule);
      dump(path.join(outRoot, rel, name), cm);
      if (cx.some(Boolean)) dump(path.join(outRoot, rel, stem + ".extra.json"), cx);
      if (cmeta.parts && !cmeta.parts.some(Boolean)) delete cmeta.parts;
      if (Object.values(cmeta).some((v) => v.some(Boolean))) dump(path.join(outRoot, rel, stem + ".meta.json"), cmeta);
    }
    const fpt = path.join(dir, "_fingerprints.txt");
    if (fs.existsSync(fpt)) { fs.mkdirSync(path.join(outRoot, rel), { recursive: true }); fs.copyFileSync(fpt, path.join(outRoot, rel, "_fingerprints.txt")); }
  }
}

function pyJson(v) {
  if (Array.isArray(v)) return "[" + v.map(pyJson).join(", ") + "]";
  if (v && typeof v === "object") return "{" + Object.entries(v).map(([k, x]) => JSON.stringify(k) + ": " + pyJson(x)).join(", ") + "}";
  return JSON.stringify(v);
}

if (typeof process !== "undefined" && process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  cli(process.argv.slice(2));
}
