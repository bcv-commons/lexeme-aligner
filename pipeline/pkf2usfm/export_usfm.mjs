#!/usr/bin/env node
/**
 * PKF -> USFM edge converter (the one Node dependency; see docs/bibles-recipe-layer.md).
 * Vendored verbatim from the bcv-query monorepo (example/scripts/export_usfm.mjs) — the working
 * reference. Reads data/_pool/<iso>/*.pkf (a Proskomma succinct docSet) and writes one USFM file
 * per book. proskomma-core's native `usfm` document field does the export — no external tool.
 *
 * Run once at the ingestion edge (never at runtime). lexeme_aligner.cdn_source invokes it via
 * subprocess, then re-numbers to <NN>-<BOOK>.json using the aligner's own book map when writing USJ,
 * so this script's file-number scheme does not need to match run_pilot's.
 *
 * Usage: node pkf2usfm/export_usfm.mjs <iso> [--out <dir>] [--collection <base>]
 *   (deps: `cd pkf2usfm && npm install`)
 */
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { decompressSync, strFromU8 } from 'fflate';
import { Proskomma } from 'proskomma-core';

// USFM/Paratext book id -> file number. 40 is reserved (skipped).
const BOOK_NUM = {
  GEN: '01', EXO: '02', LEV: '03', NUM: '04', DEU: '05', JOS: '06', JDG: '07',
  RUT: '08', '1SA': '09', '2SA': '10', '1KI': '11', '2KI': '12', '1CH': '13',
  '2CH': '14', EZR: '15', NEH: '16', EST: '17', JOB: '18', PSA: '19', PRO: '20',
  ECC: '21', SNG: '22', ISA: '23', JER: '24', LAM: '25', EZK: '26', DAN: '27',
  HOS: '28', JOL: '29', AMO: '30', OBA: '31', JON: '32', MIC: '33', NAM: '34',
  HAB: '35', ZEP: '36', HAG: '37', ZEC: '38', MAL: '39',
  MAT: '41', MRK: '42', LUK: '43', JHN: '44', ACT: '45', ROM: '46', '1CO': '47',
  '2CO': '48', GAL: '49', EPH: '50', PHP: '51', COL: '52', '1TH': '53',
  '2TH': '54', '1TI': '55', '2TI': '56', TIT: '57', PHM: '58', HEB: '59',
  JAS: '60', '1PE': '61', '2PE': '62', '1JN': '63', '2JN': '64', '3JN': '65',
  JUD: '66', REV: '67',
  // deuterocanon
  TOB: '68', JDT: '69', ESG: '70', WIS: '71', SIR: '72', BAR: '73', LJE: '74',
  S3Y: '75', SUS: '76', BEL: '77', '1MA': '78', '2MA': '79', '3MA': '80',
  '4MA': '81', '1ES': '82', '2ES': '83', MAN: '84', PS2: '85', ODA: '86',
  PSS: '87',
  // peripherals + user-defined "extra material" books (XXA-XXG)
  FRT: 'A0', BAK: 'A1', OTH: 'A2', INT: 'A7', CNC: 'A8', GLO: 'A9', TDX: 'B0',
  NDX: 'B1', XXA: '94', XXB: '95', XXC: '96', XXD: '97', XXE: '98', XXF: '99',
  XXG: '100',
};

class SABProskomma extends Proskomma {
  constructor() {
    super();
    this.selectors = [
      { name: 'lang', type: 'string', regex: '^[A-Za-z0-9-]{2,30}$' },
      { name: 'abbr', type: 'string', regex: '^[A-Za-z0-9 -]+$' },
    ];
    this.validateSelectors();
  }
}

function parseArgs(argv) {
  const out = { iso: null, out: null, collection: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--out') out.out = argv[++i];
    else if (a === '--collection') out.collection = argv[++i];
    else if (!a.startsWith('--') && !out.iso) out.iso = a;
  }
  return out;
}

function exportPkf(pkfPath, outDir) {
  const pk = new SABProskomma();
  pk.loadSuccinctDocSet(
    JSON.parse(strFromU8(decompressSync(new Uint8Array(readFileSync(pkfPath)))))
  );
  const dsId = pk.gqlQuerySync('{docSets{id}}').data.docSets[0].id;
  const docs = pk.gqlQuerySync(
    `{docSet(id:"${dsId}"){documents{bookCode:header(id:"bookCode")}}}`
  ).data.docSet.documents;

  mkdirSync(outDir, { recursive: true });
  let n = 0;
  const unmapped = [];
  for (const { bookCode } of docs) {
    const usfm = pk.gqlQuerySync(
      `{docSet(id:"${dsId}"){document(bookCode:"${bookCode}"){ usfm }}}`
    ).data.docSet.document.usfm;
    const num = BOOK_NUM[bookCode];
    if (!num) unmapped.push(bookCode);
    const name = num ? `${num}-${bookCode}.usfm` : `${bookCode}.usfm`;
    writeFileSync(join(outDir, name), usfm);
    n++;
  }
  return { n, unmapped };
}

function main() {
  const { iso, out, collection } = parseArgs(process.argv.slice(2));
  if (!iso) {
    console.error('usage: node pkf2usfm/export_usfm.mjs <iso> [--out <dir>] [--collection <base>]');
    process.exit(2);
  }
  const poolDir = join('data', '_pool', iso);
  let pkfs;
  try {
    pkfs = readdirSync(poolDir).filter((f) => f.endsWith('.pkf'));
  } catch {
    console.error(`[usfm] no pool dir data/_pool/${iso}/`);
    process.exit(1);
  }
  if (collection) pkfs = pkfs.filter((f) => f.startsWith(`${collection}.`));
  if (pkfs.length === 0) {
    console.error(`[usfm] no matching .pkf in ${poolDir}/`);
    process.exit(1);
  }

  const baseOut = out || join('temp', `usfm-${iso}`);
  const multi = pkfs.length > 1;
  for (const pkf of pkfs) {
    const base = pkf.split('.')[0]; // collection base name, e.g. niy_C01
    const dir = multi ? join(baseOut, base) : baseOut;
    const { n, unmapped } = exportPkf(join(poolDir, pkf), dir);
    let msg = `[usfm] ${base}: ${n} book(s) -> ${dir}/`;
    if (unmapped.length) msg += `  (unknown book code, named without a number prefix: ${unmapped.join(', ')})`;
    console.log(msg);
  }
}

main();
