// Reads the active Photoshop document into the same layer `model` the Photopea panel used, plus a
// path -> real UXP layer map so changes apply by direct reference (no re-walk). Ported from the
// FETCH script in frontend/public/photopea-plugin.html — same classifier, same roles, same style
// detection (LOGO group => CL2K, SEQUEL group => MM2K). UXP mapping: LayerSet -> kind===GROUP,
// L.layers -> group children, L.name/L.visible unchanged. Layer collections are iterated by index
// (UXP's Layers collection is array-like but not guaranteed to have .forEach).
'use strict';

const { key } = require('./toggle');

// Root-level poster variants that aren't seasons (matched at any depth, like the original fs()).
const SING = [
  { re: /^specials$/i, lab: 'SP' },
  { re: /^collection$/i, lab: 'C' },
  { re: /^complete limited/i, lab: 'CLS' },
];

// Role classifier — verbatim from the Photopea FETCH `cls()`.
function classify(layer, parentRole, isGroup) {
  if (parentRole === 'main') {
    if (isGroup) {
      if (/\d+\s*-\s*\d+/.test(layer.name)) return 'decade';
      if (/years?/i.test(layer.name)) return 'years';   // "SEASON YEARS TEXT" / "SEASONS YEAR TEXT"
      return 'group';
    }
    return 'otherLeaf';
  }
  if (parentRole === 'decade') return isGroup ? 'group' : 'season';
  if (parentRole === 'years') return 'year';
  return 'other';
}

// Chip label for a season/decade node — "SEASON 3 TEXT" -> "3", "1990-1999" -> "S1990-1999".
function label(name) {
  const r = name.replace(/seasons?|text/ig, '').trim();
  if (r === '') return 'S';
  if (r.includes('-')) return 'S' + r.replace(/\s+/g, '');
  return r.replace(/\s+/g, '');
}
// Sequel chip label: "SEQUEL 3" -> "3".
function seqLabel(name) { const m = name.match(/(\d+)/); return m ? m[1] : (name.replace(/sequels?/ig, '').trim() || 'S'); }

function readModel(doc, constants) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  const layerByPath = {};
  const record = (p, L) => { layerByPath[key(p)] = L; };

  // ---- singles: record EVERY layer (so every ancestor path is mapped) + collect variant leaves ----
  const singles = [];
  function scan(layers, pa, pv) {
    for (let i = 0; i < layers.length; i++) {
      const L = layers[i], here = pa.concat(i);
      record(here, L);
      const ev = pv && L.visible;
      for (const s of SING) if (s.re.test(L.name)) singles.push({ lab: s.lab, n: L.name, p: here, v: ev, rv: L.visible });
      if (isGroup(L)) scan(L.layers, here, ev);
    }
  }
  scan(doc.layers, [], true);

  // ---- first group matching `re`, searched depth-first ----
  function findGroup(layers, pa, re) {
    for (let i = 0; i < layers.length; i++) {
      const L = layers[i], here = pa.concat(i);
      if (isGroup(L)) { if (re.test(L.name)) return { L, p: here }; const f = findGroup(L.layers, here, re); if (f) return f; }
    }
    return null;
  }

  // ---- SEASONS group tree (roles) ----
  const seasons = [];
  function walk(node, path, role, pv) {
    const ev = pv && node.visible;
    seasons.push({ n: node.name, p: path, g: isGroup(node), v: ev, r: role, rv: node.visible });
    if (isGroup(node)) for (let i = 0; i < node.layers.length; i++) {
      const c = node.layers[i];
      walk(c, path.concat(i), classify(c, role, isGroup(c)), ev);
    }
  }
  const root = findGroup(doc.layers, [], /^\s*seasons\s*$/i);
  if (root) walk(root.L, root.p, 'main', true);

  // ---- SEQUEL group (MM2K): numbered leaves become toggle chips ----
  const sequels = [];
  const sq = findGroup(doc.layers, [], /^\s*sequels?\s*$/i);
  if (sq) {
    const gv = sq.L.visible;
    for (let i = 0; i < sq.L.layers.length; i++) {
      const c = sq.L.layers[i];
      if (!isGroup(c)) sequels.push({ n: c.name, p: sq.p.concat(i), v: gv && c.visible, rv: c.visible });
    }
  }
  const seqGroup = sq ? sq.p : null;

  // ---- style: LOGO group => CL2K, else SEQUEL group => MM2K (mutually exclusive across templates) ----
  const lg = findGroup(doc.layers, [], /^\s*logos?\s*$/i);
  const style = lg ? 'CL2K' : (sq ? 'MM2K' : '');

  return { model: { singles, seasons, sequels, seqGroup }, layerByPath, style, logoGroup: lg ? lg.p : null };
}

// Suffix for the JPG filename from whatever variant is currently visible (mirror of activeSuffix
// in the Photopea panel): a single (Specials / Complete-Limited-as-S1) wins; else the on season.
function activeSuffix(model) {
  const single = model.singles.find((s) => s.v);
  if (single) {
    if (single.lab === 'SP') return ' - Specials';
    if (single.lab === 'CLS') return ' - Season 1';
    return '';
  }
  const main = model.seasons.find((n) => n.r === 'main');
  const season = model.seasons.find((n) => {
    if (n.r !== 'season' || !n.v) return false;
    const dkey = key(n.p.slice(0, -1));
    const decade = model.seasons.find((m) => m.r === 'decade' && key(m.p) === dkey);
    return (!decade || decade.v) && (!main || main.v);
  });
  return season ? ' - Season ' + label(season.n) : '';
}

// Batch scan: collect the name-tag candidate layers (s0/s1…/s1-8 ranges/main/show/movie/poster/c/cls
// or a layer named like the PSD) with raw visibility, plus the pairable text layers ("Season N",
// "Specials", "Complete Limited Series", "Collection"), anywhere in the tree. batch.buildTagItems
// turns these into export items. Paths align with readModel's layerByPath (identical walk order).
function scanBatch(doc, constants, baseNorm) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  const norm = (s) => ('' + s).toLowerCase().replace(/^\s+|\s+$/g, '').replace(/\s+/g, ' ');
  const variants = [], seasonText = [], specialsText = [], clsText = [], collectionText = [];
  const TAG = /^s\d{1,4}(?:-\d{1,4})?$/;
  const isAlias = (nm) => nm === 'show' || nm === 'movie' || nm === 'poster' || nm === 'main' || (!!baseNorm && nm === baseNorm);

  // Tags live in the root-level POSTER group when one exists (the template's poster container —
  // exactly the jsx's rule). Only its DIRECT children are candidates there, where "poster"/PSD-name
  // aliases are unambiguous. Without a POSTER group, fall back to a whole-tree scan — but skip the
  // "poster" alias there, so the template's own POSTER group can never be swallowed as a variant
  // (and then hidden by every export).
  let pg = -1;
  for (let i = 0; i < doc.layers.length; i++) {
    if (isGroup(doc.layers[i]) && norm(doc.layers[i].name) === 'poster') { pg = i; break; }
  }
  if (pg >= 0) {
    const kids = doc.layers[pg].layers;
    for (let i = 0; i < kids.length; i++) {
      const nm = norm(kids[i].name);
      if (TAG.test(nm) || nm === 'c' || nm === 'cls' || isAlias(nm)) {
        variants.push({ nm: (!!baseNorm && nm === baseNorm) ? 'show' : nm, p: [pg, i], v: kids[i].visible });
      }
    }
  }
  function sc(layers, pa) {
    for (let i = 0; i < layers.length; i++) {
      const L = layers[i], here = pa.concat(i);
      const nm = norm(L.name);
      if (pg < 0 && (TAG.test(nm) || nm === 'show' || nm === 'movie' || nm === 'main'
          || nm === 'c' || nm === 'cls' || (!!baseNorm && nm === baseNorm))) {
        variants.push({ nm: (!!baseNorm && nm === baseNorm) ? 'show' : nm, p: here, v: L.visible });
      }
      const sm = nm.match(/^season\s*0*(\d+)$/);
      if (sm) seasonText.push({ n: parseInt(sm[1], 10), p: here });
      else if (nm === 'special' || nm === 'specials') specialsText.push({ p: here });
      else if (/^complete limited/.test(nm)) clsText.push({ p: here });
      else if (nm === 'collection') collectionText.push({ p: here });
      if (isGroup(L)) sc(L.layers, here);
    }
  }
  sc(doc.layers, []);
  return { variants, seasonText, specialsText, clsText, collectionText };
}

module.exports = { readModel, scanBatch, label, seqLabel, activeSuffix };
