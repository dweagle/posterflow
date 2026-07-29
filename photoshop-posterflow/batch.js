// Pure batch item-building logic — ported from the Photopea panel (and its mirror
// frontend/src/lib/photopeaBatch.ts). Every function returns { suffix, changes } items whose `changes`
// are the same { p, v } visibility lists toggle.js produces; main.js applies them + exports one JPG
// each. No UXP here. Two modes: name-tag convention (s0/s1…/main/show/c) and a season/year range.
'use strict';

const { key, byRole, main, seqOff, seqGroupOff, yearsOff } = require('./toggle');

const seasonNumOf = (S) => { const m = (S.n || '').match(/(\d+)/); return m ? parseInt(m[1], 10) : NaN; };

// Always-ON visibility for a season leaf (mirror clickSeason without the toggle-off branch).
const seasonChanges = (model, S) => {
  const parentKey = key(S.p.slice(0, -1));
  const changes = [{ p: S.p, v: true }];
  if (main(model)) changes.push({ p: main(model).p, v: true });
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: key(dn.p) === parentKey }));
  byRole(model, 'season').forEach((sn) => { if (sn !== S) changes.push({ p: sn.p, v: false }); });
  model.singles.forEach((si) => changes.push({ p: si.p, v: false }));
  changes.push(...yearsOff(model), ...seqOff(model), ...seqGroupOff(model));
  return changes;
};

// Always-ON for a "Season YYYY" year leaf (lives under a "years" group, not a decade).
const yearChanges = (model, Y) => {
  const parentKey = key(Y.p.slice(0, -1));
  const changes = [{ p: Y.p, v: true }];
  if (main(model)) changes.push({ p: main(model).p, v: true });
  byRole(model, 'years').forEach((yg) => changes.push({ p: yg.p, v: key(yg.p) === parentKey }));
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
  byRole(model, 'season').forEach((sn) => changes.push({ p: sn.p, v: false }));
  byRole(model, 'year').forEach((yn) => { if (yn !== Y) changes.push({ p: yn.p, v: false }); });
  model.singles.forEach((si) => changes.push({ p: si.p, v: false }));
  changes.push(...seqOff(model), ...seqGroupOff(model));
  return changes;
};

const singleChanges = (model, SI) => {
  const changes = [{ p: SI.p, v: true }];
  for (let i = 1; i < SI.p.length; i++) changes.push({ p: SI.p.slice(0, i), v: true });
  model.singles.forEach((x) => { if (x !== SI) changes.push({ p: x.p, v: false }); });
  byRole(model, 'season').forEach((sn) => changes.push({ p: sn.p, v: false }));
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
  changes.push(...yearsOff(model), ...seqOff(model), ...seqGroupOff(model));
  return changes;
};

// Convention variant → suffix + visibility: show the s-layer (+ ancestors), hide the other convention
// leaves, and switch on the matching "Season N"/"Specials" text so each file shows its number.
const conventionSuffix = (v) => v.t === 's' ? (v.n === 0 ? ' - Specials' : ' - Season ' + v.n) : '';
const showText = (changes, t, on) => { changes.push({ p: t.p, v: on }); if (on) for (let i = 1; i < t.p.length; i++) changes.push({ p: t.p.slice(0, i), v: true }); };

const buildConventionItems = (variants, seasonText, specialsText) => variants.map((v) => {
  const changes = [];
  variants.forEach((o) => changes.push({ p: o.p, v: false }));
  changes.push({ p: v.p, v: true });
  for (let i = 1; i < v.p.length; i++) changes.push({ p: v.p.slice(0, i), v: true });
  if (v.t === 's' && v.n >= 1) {
    seasonText.forEach((t) => showText(changes, t, t.n === v.n));
    specialsText.forEach((t) => changes.push({ p: t.p, v: false }));
  } else if (v.t === 's' && v.n === 0) {
    seasonText.forEach((t) => changes.push({ p: t.p, v: false }));
    specialsText.forEach((t) => showText(changes, t, true));
  } else {
    seasonText.forEach((t) => changes.push({ p: t.p, v: false }));
    specialsText.forEach((t) => changes.push({ p: t.p, v: false }));
  }
  return { suffix: conventionSuffix(v), changes };
});

const parseRange = (str) => {
  const s = (str || '').trim();
  if (!s) return { start: null, end: null };
  if (s.indexOf('-') >= 0) { const parts = s.split('-'); const a = parseInt(parts[0], 10), b = parseInt(parts[1], 10); return { start: isNaN(a) ? 1 : a, end: isNaN(b) ? 9999 : b }; }
  const n = parseInt(s, 10);
  return isNaN(n) ? { start: null, end: null } : { start: 1, end: n };
};

const seasonItems = (model, start, end) => {
  const items = [];
  byRole(model, 'season').slice().sort((a, b) => (seasonNumOf(a) || 0) - (seasonNumOf(b) || 0)).forEach((S) => {
    const n = seasonNumOf(S);
    if (isNaN(n)) return;
    if ((start != null && n < start) || (end != null && n > end)) return;
    items.push({ suffix: ' - Season ' + n, changes: seasonChanges(model, S) });
  });
  // Specials = "season 0": include only when 0 is in range (or "all"). Never Collection / CLS.
  if ((start == null) || (start <= 0 && (end == null || end >= 0))) {
    const SP = model.singles.find((s) => s.lab === 'SP');
    if (SP) items.push({ suffix: ' - Specials', changes: singleChanges(model, SP) });
  }
  return items;
};

const isYearRange = (r) => (r.start != null && r.start >= 1900) || (r.end != null && r.end >= 1900);
const yearItems = (model, start, end) => {
  const items = [];
  byRole(model, 'year').slice().sort((a, b) => (seasonNumOf(a) || 0) - (seasonNumOf(b) || 0)).forEach((Y) => {
    const n = seasonNumOf(Y);
    if (isNaN(n)) return;
    if ((start != null && n < start) || (end != null && n > end)) return;
    items.push({ suffix: ' - Season ' + n, changes: yearChanges(model, Y) });
  });
  return items;
};

// ---- Name-tag mode: the tag language, ported from CL2K batch export.jsx ----
// Mirror of buildTagItems in frontend/src/lib/photopeaBatch.ts (the tested spec) — keep in sync.

const MAX_RANGE = 30;   // a range tag (s1-8) may expand to at most this many exports

const normName = (s) => String(s).toLowerCase().replace(/^\s+|\s+$/g, '').replace(/\s+/g, ' ');

// "Title Collection" / "Title Collection {tmdb-1}" → the c export needs no " - Collection" suffix.
const baseIsCollection = (base) =>
  /collection$/i.test(String(base).replace(/\s*\{[^}]*\}\s*/g, ' ').replace(/\s+/g, ' ').trim());

const seasonTag = (n) =>
  n === 0 ? { key: 's0', kind: 's', n: 0, sort: 100000 } : { key: 's' + n, kind: 's', n, sort: n };

// One layer name → export tags. Aliases → main; s{N}; s{A}-{B} ranges (rejected with a reason when
// backwards or wider than MAX_RANGE); c → Collection; cls → Complete Limited Series (Season 1 file).
function parseTagName(nm, baseNorm) {
  if (nm === 'show' || nm === 'movie' || nm === 'poster' || nm === 'main' || (!!baseNorm && nm === baseNorm))
    return { tags: [{ key: 'show', kind: 'main', n: -1, sort: 0 }], reject: null };
  if (nm === 'c') return { tags: [{ key: 'c', kind: 'c', n: -1, sort: 200000 }], reject: null };
  if (nm === 'cls') return { tags: [{ key: 'cls', kind: 'cls', n: 1, sort: 1.5 }], reject: null };
  const m = /^s(\d{1,4})(?:-(\d{1,4}))?$/.exec(nm);
  if (m) {
    const start = parseInt(m[1], 10);
    if (!m[2]) return { tags: [seasonTag(start)], reject: null };
    const end = parseInt(m[2], 10);
    if (end <= start) return { tags: [], reject: '"' + nm + '" was skipped: a range has to count upwards, like "s' + start + '-' + (start + 1) + '"' };
    if (end - start + 1 > MAX_RANGE) return { tags: [], reject: '"' + nm + '" was skipped: it covers ' + (end - start + 1) + ' seasons, more than the ' + MAX_RANGE + ' a single range may expand to' };
    const out = [];
    for (let v = start; v <= end; v++) out.push(seasonTag(v));
    return { tags: out, reject: null };
  }
  return { tags: [], reject: null };
}

const showTextChange = (changes, t, on) => {
  changes.push({ p: t.p, v: on });
  if (on) for (let i = 1; i < t.p.length; i++) changes.push({ p: t.p.slice(0, i), v: true });
};

// Scanned candidates → export items. Duplicates: topmost VISIBLE wins (topmost overall if none), with
// a warning. Sorted main → seasons ascending (cls beside s1) → Specials → Collection. Each item shows
// its layer (+ancestors), hides other candidates, pairs the right text layer, hides the rest.
// texts = { season, specials, cls, collection }; `collection: true` marks the c export.
function buildTagItems(variants, texts, baseName) {
  const warnings = [];
  const baseNorm = normName(baseName);
  const byKey = {}, keyOrder = [];
  variants.forEach((rv) => {
    const parsed = parseTagName(rv.nm, baseNorm);
    if (parsed.reject) warnings.push(parsed.reject);
    parsed.tags.forEach((tag) => {
      if (!byKey[tag.key]) { byKey[tag.key] = []; keyOrder.push(tag.key); }
      byKey[tag.key].push({ layer: rv, tag });
    });
  });

  const chosen = [], seen = {}, warnedCombos = {};
  keyOrder.forEach((k) => {
    const cands = byKey[k];
    let pick = cands[0];
    if (cands.length > 1) {
      const vis = cands.find((c) => c.layer.v);
      if (vis) pick = vis;
      const names = [];
      cands.forEach((c) => { if (names.indexOf(c.layer.nm) < 0) names.push(c.layer.nm); });
      const combo = names.join('|');
      if (names.length > 1 && !warnedCombos[combo]) {
        warnedCombos[combo] = true;
        warnings.push(cands.length + ' layers are tagged "' + names.join('" / "') + '" — exported ' +
          (vis ? 'the topmost visible one' : 'the topmost one (none were visible)') + ' and ignored the rest.');
      }
    }
    seen[k] = true;
    chosen.push(pick);
  });
  if (seen['cls'] && seen['s1'])
    warnings.push('Both "s1" and "cls" are tagged — they export to the same " - Season 1" file, so the later one overwrites the other.');

  chosen.sort((a, b) => a.tag.sort - b.tag.sort);
  const collectionBase = baseIsCollection(baseName);

  const items = chosen.map(({ layer, tag }) => {
    const changes = [];
    variants.forEach((o) => changes.push({ p: o.p, v: false }));
    changes.push({ p: layer.p, v: true });
    for (let i = 1; i < layer.p.length; i++) changes.push({ p: layer.p.slice(0, i), v: true });
    const onOff = (list, onFor) => list.forEach((t) => (onFor(t) ? showTextChange(changes, t, true) : changes.push({ p: t.p, v: false })));
    onOff(texts.season, (t) => tag.kind === 's' && tag.n >= 1 && t.n === tag.n);
    onOff(texts.specials, () => tag.kind === 's' && tag.n === 0);
    onOff(texts.cls, () => tag.kind === 'cls');
    onOff(texts.collection, () => tag.kind === 'c');
    const suffix = tag.kind === 'main' ? ''
      : tag.kind === 'c' ? (collectionBase ? '' : ' - Collection')
      : tag.kind === 'cls' ? ' - Season 1'
      : tag.n === 0 ? ' - Specials' : ' - Season ' + tag.n;
    return { suffix, changes, collection: tag.kind === 'c', key: tag.key };
  });
  return { items, warnings };
}

// Parse the tags-mode filter input: blank → null (run everything); otherwise a set of tag keys —
// tokens like "c", "cls", "main"/"show", "s3", "3", "s1-8" (comma/space separated).
const parseTagFilter = (str) => {
  const toks = String(str || '').toLowerCase().split(/[\s,]+/).filter(Boolean);
  if (!toks.length) return null;
  const keys = {};
  toks.forEach((t) => {
    if (t === 'c') keys.c = 1;
    else if (t === 'cls') keys.cls = 1;
    else if (t === 'main' || t === 'show' || t === 'movie' || t === 'poster') keys.show = 1;
    else {
      const m = /^s?(\d{1,4})(?:-s?(\d{1,4}))?$/.exec(t);
      if (m) {
        const a = parseInt(m[1], 10);
        const b = m[2] ? parseInt(m[2], 10) : a;
        const lo = Math.min(a, b), hi = Math.max(a, b);
        for (let n = lo; n <= hi && n - lo < 60; n++) keys['s' + n] = 1;
      }
    }
  });
  return keys;
};

module.exports = {
  seasonNumOf, seasonChanges, yearChanges, singleChanges,
  conventionSuffix, buildConventionItems, parseRange, seasonItems, yearItems, isYearRange,
  MAX_RANGE, normName, baseIsCollection, parseTagName, buildTagItems, parseTagFilter,
};
