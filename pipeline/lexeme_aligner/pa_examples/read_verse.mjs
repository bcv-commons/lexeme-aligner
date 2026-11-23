// Print one verse of bcv-commons/part-alignments: every source part and the target words it links to, then the whole-word view
// (= compact-alignments, computed here by fold.mjs). No dependencies; published beside fold.mjs and tokenize.js. Same output as
// read_verse.py.
//
//   node read_verse.mjs ROOT ISO EDITION "MRK 1:1" "the verse text as the edition has it"
//
// In a browser: fetch the same three files (manifest.json, _index/<BOOK>_parts.json, the edition's book file) and call report().
import { entries, foldVerse, positions } from "./fold.mjs";
import { tokenize } from "./tokenize.js";

const pad = (s, n) => String(s).padEnd(n);
const lpad = (s, n) => String(s).padStart(n);

function wordsOf(span, toks) {
  if (span === "_") return "(checked: renders nothing)";
  return positions(span).map((p) => (p < toks.length ? toks[p] : `#${p}`)).join(" ");
}

export function report(verse, main, meta, toks) {
  const out = ["source parts:"];
  const links = entries(main);
  verse.units.forEach(([pid, kind, text], o) => {
    out.push(`  ${lpad(o, 3)}  ${pad(pid, 16)} ${pad(kind, 14)} ${pad(text, 12)} -> ${links.has(o) ? wordsOf(links.get(o), toks) : "-"}`);
  });
  const [cmain] = foldVerse(verse, main, meta);
  const cl = entries(cmain);
  out.push("whole words (compact-alignments view, by the fold):");
  let cord = -1;
  verse.tokens.forEach(([key, lexeme, content], k) => {
    const word = verse.units.filter((u) => u[3] === k).map((u) => u[2]).join("");
    if (!content) {
      out.push(`    -  ${pad(word, 14)} ${pad(lexeme, 10)} (function word: no slot in compact)`);
      return;
    }
    cord += 1;
    out.push(`  ${lpad(cord, 3)}  ${pad(word, 14)} ${pad(lexeme, 10)} -> ${cl.has(cord) ? wordsOf(cl.get(cord), toks) : "-"}`);
  });
  return out;
}

async function main(argv) {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const [root, iso, edition, ref, text] = argv;
  const book = ref.split(" ")[0];
  const load = (fp) => JSON.parse(fs.readFileSync(fp, "utf8"));
  const index = load(path.join(root, "_index", `${book}_parts.json`));
  const name = load(path.join(root, "manifest.json")).languages[iso].editions[edition].books[book];
  const folder = path.join(root, iso[0], iso, edition);
  const main = load(path.join(folder, name));
  const metaFp = path.join(folder, name.replace(".json", ".meta.json"));
  const metaAll = fs.existsSync(metaFp) ? load(metaFp) : {};
  const i = Object.keys(index).indexOf(ref);
  const meta = Object.fromEntries(Object.entries(metaAll).map(([c, v]) => [c, v[i]]));
  console.log(`${ref}  ${edition}`);
  console.log(report(index[ref], main[i], meta, tokenize(text)).join("\n"));
}

if (typeof process !== "undefined" && process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main(process.argv.slice(2));
}
