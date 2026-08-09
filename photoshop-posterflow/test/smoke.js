// Node smoke test for the document-independent logic (no Photoshop needed).
// Mocks the photoshop `constants` + a layer tree and exercises readModel / toggles / activeSuffix.
// Run: node photoshop-posterflow/test/smoke.js
'use strict';

const T = require('../toggle');
const M = require('../model');
const B = require('../batch');
const G = require('../geometry');

// --- mock photoshop constants + layer factory ---
const constants = { LayerKind: { GROUP: 'group' } };
const grp = (name, layers, visible = false) => ({ name, visible, kind: 'group', layers });
const lyr = (name, visible = false) => ({ name, visible, kind: 'pixel' });

// CL2K: LOGO group (⇒ CL2K), a SEASONS group with two decades, and two singles.
const cl2k = {
  name: 'Show (2020) {tmdb-123}.psd',
  layers: [
    grp('LOGO', [lyr('logo art', true)], true),
    grp('SEASONS', [
      grp('1990-1999', [lyr('Season 1'), lyr('Season 2')]),
      grp('2000-2009', [lyr('Season 3')]),
    ]),
    lyr('Specials'),
    lyr('Collection'),
  ],
};

// MM2K: SEQUEL group (⇒ MM2K) + a SEASONS group.
const mm2k = {
  name: 'Saga (1999) {tmdb-9}.psd',
  layers: [
    grp('SEQUELS', [lyr('SEQUEL 1'), lyr('SEQUEL 2'), lyr('SEQUEL 3')]),
    grp('SEASONS', [ grp('1990-1999', [lyr('Season 1'), lyr('Season 2')]) ]),
  ],
};

// Apply a toggle.js changes list to the model's own .v flags (mirror of main.js applyChanges).
const apply = (model, changes) =>
  changes.forEach((ch) => { const k = T.key(ch.p); T.allNodes(model).forEach((n) => { if (T.key(n.p) === k) n.v = ch.v; }); });

let pass = 0, fail = 0;
const ok = (cond, msg) => { if (cond) { pass++; } else { fail++; console.error('  FAIL:', msg); } };

// ---- CL2K ----
{
  const { model, style, logoGroup } = M.readModel(cl2k, constants);
  ok(style === 'CL2K', 'CL2K style detected (got ' + style + ')');
  ok(Array.isArray(logoGroup) && logoGroup.length > 0, 'CL2K exposes the LOGO group path');
  ok(model.singles.length === 2, 'CL2K has 2 singles (SP, C)');
  ok(model.seasons.some((n) => n.r === 'main'), 'CL2K has a main SEASONS node');
  ok(model.seasons.filter((n) => n.r === 'decade').length === 2, 'CL2K has 2 decades');
  ok(model.seasons.filter((n) => n.r === 'season').length === 3, 'CL2K has 3 season leaves');
  ok(model.sequels.length === 0, 'CL2K has no sequels');

  const s3 = model.seasons.find((n) => n.r === 'season' && /3/.test(n.n));
  apply(model, T.clickSeason(model, s3));
  ok(M.activeSuffix(model) === ' - Season 3', 'clicking Season 3 → suffix " - Season 3" (got "' + M.activeSuffix(model) + '")');
  // other decade hidden, its own decade + main shown, singles hidden
  const d90 = model.seasons.find((n) => n.r === 'decade' && /1990/.test(n.n));
  const d00 = model.seasons.find((n) => n.r === 'decade' && /2000/.test(n.n));
  ok(d00.v === true && d90.v === false, 'Season 3 shows its decade, hides the other');
  ok(model.seasons.find((n) => n.r === 'main').v === true, 'main SEASONS shown');
  ok(model.singles.every((s) => s.v === false), 'singles hidden when a season is on');

  const sp = model.singles.find((s) => s.lab === 'SP');
  apply(model, T.clickSingle(model, sp));
  ok(M.activeSuffix(model) === ' - Specials', 'clicking Specials → suffix " - Specials"');
  ok(model.seasons.filter((n) => n.r === 'season').every((n) => n.v === false), 'seasons hidden when Specials on');
}

// ---- MM2K ----
{
  const { model, style, logoGroup } = M.readModel(mm2k, constants);
  ok(style === 'MM2K', 'MM2K style detected (got ' + style + ')');
  ok(logoGroup === null, 'MM2K has no LOGO group path');
  ok(model.sequels.length === 3, 'MM2K has 3 sequels');
  ok(model.seqGroup !== null, 'MM2K seqGroup path captured');

  const q2 = model.sequels.find((s) => M.seqLabel(s.n) === '2');
  apply(model, T.clickSequel(model, q2));
  ok(q2.v === true, 'SEQUEL 2 on');
  ok(model.sequels.filter((s) => s !== q2).every((s) => s.v === false), 'other sequels off');
  ok(model.seasons.find((n) => n.r === 'main').v === false, 'main SEASONS hidden when a sequel is on');
}

// ---- year chips (Season YYYY under the years group) ----
{
  const doc = {
    name: 'Longrunner (1990).psd',
    layers: [
      grp('LOGO', [lyr('logo art', true)], true),
      grp('SEASONS', [
        grp('1990-1999', [lyr('Season 1'), lyr('Season 2')]),
        grp('SEASON YEARS TEXT', [lyr('Season 2015'), lyr('Season 2016')]),
      ]),
      lyr('Specials'),
    ],
  };
  const { model } = M.readModel(doc, constants);
  ok(model.seasons.filter((n) => n.r === 'years').length === 1, 'years group classified');
  ok(model.seasons.filter((n) => n.r === 'year').length === 2, 'two year leaves classified');

  const y15 = model.seasons.find((n) => n.r === 'year' && /2015/.test(n.n));
  apply(model, T.clickYear(model, y15));
  ok(y15.v === true, 'clicking a year turns it on');
  ok(model.seasons.find((n) => n.r === 'years').v === true, 'its years group opens');
  ok(model.seasons.find((n) => n.r === 'main').v === true, 'main SEASONS shown');
  ok(model.seasons.filter((n) => n.r === 'decade').every((d) => d.v === false), 'decades hidden for a year');
  ok(model.seasons.filter((n) => n.r === 'season').every((s) => s.v === false), 'numbered seasons hidden for a year');

  const s1 = model.seasons.find((n) => n.r === 'season' && /1/.test(n.n));
  apply(model, T.clickSeason(model, s1));
  ok(y15.v === false && model.seasons.find((n) => n.r === 'years').v === false, 'clicking a season hides the year + years group');

  // Singular folder spelling also classifies
  const doc2 = { name: 'X.psd', layers: [grp('SEASONS', [grp('SEASONS YEAR TEXT', [lyr('Season 2020')])])] };
  const m2 = M.readModel(doc2, constants).model;
  ok(m2.seasons.filter((n) => n.r === 'year').length === 1, 'singular "YEAR" folder spelling classified');
}

// ---- batch: name-tag language (jsx port) ----
{
  const doc = {
    name: 'Toon (2001) {tmdb-5}.psd',
    layers: [
      grp('POSTERS', [lyr('main', true), lyr('s0'), lyr('s1'), lyr('s3-4'), lyr('c')]),
      grp('TEXT', [lyr('Season 1'), lyr('Season 3'), lyr('Specials'), lyr('COLLECTION'), lyr('Complete Limited Series')]),
    ],
  };
  const scan = M.scanBatch(doc, constants, B.normName('Toon (2001) {tmdb-5}'));
  ok(scan.variants.length === 5, 'scan found 5 tag candidates (got ' + scan.variants.length + ')');
  ok(scan.seasonText.length === 2 && scan.specialsText.length === 1, 'scan found season + specials text');
  ok(scan.clsText.length === 1 && scan.collectionText.length === 1, 'scan found CLS + COLLECTION text');

  const built = B.buildTagItems(scan.variants,
    { season: scan.seasonText, specials: scan.specialsText, cls: scan.clsText, collection: scan.collectionText },
    'Toon (2001) {tmdb-5}');
  const suffixes = built.items.map((it) => it.suffix);
  ok(JSON.stringify(suffixes) === JSON.stringify(['', ' - Season 1', ' - Season 3', ' - Season 4', ' - Specials', ' - Collection']),
    'sorted suffixes incl. expanded range (got ' + JSON.stringify(suffixes) + ')');
  ok(built.items[built.items.length - 1].collection === true, 'c item flagged for collection logo placement');
  ok(built.warnings.length === 0, 'no warnings for a clean doc');

  const dup = B.buildTagItems(
    [{ nm: 's1', p: [0], v: false }, { nm: 's01', p: [1], v: true }],
    { season: [], specials: [], cls: [], collection: [] }, 'X');
  ok(dup.items.length === 1 && dup.warnings.length === 1, 'duplicate tags → one item + one warning');

  const coll = B.buildTagItems([{ nm: 'c', p: [0], v: true }],
    { season: [], specials: [], cls: [], collection: [] }, 'Back to the Future Collection {tmdb-264}');
  ok(coll.items[0].suffix === '', 'collection-named PSD → c exports with no extra suffix');

  const rej = B.parseTagName('s9-1', '');
  ok(rej.tags.length === 0 && /count upwards/.test(rej.reject), 'backwards range rejected with reason');
  ok(B.parseTagName('movie', '').tags[0].kind === 'main', 'movie alias → main');
  ok(B.parseTagName('cls', '').tags[0].kind === 'cls', 'cls tag parsed');
}

// ---- batch scan: template-shaped doc with a POSTER group (regression: the POSTER group itself
// must NEVER become a variant — that hid the whole poster on every export) ----
{
  const doc = {
    name: 'Show (2020) {tmdb-1}.psd',
    layers: [
      grp('POSTER', [lyr('show', true), lyr('s1'), lyr('poster')], true),
      grp('SEASONS', [lyr('Season 1')]),
      grp('GRADIENT', [lyr('grad', true)], true),
      lyr('s2'),   // stray tag OUTSIDE the POSTER group — ignored when a POSTER group exists
    ],
  };
  const scan = M.scanBatch(doc, constants, B.normName('Show (2020) {tmdb-1}'));
  ok(scan.variants.length === 3, 'POSTER-group mode: only its direct children are candidates (got ' + scan.variants.length + ')');
  ok(scan.variants.every((v) => v.p.length === 2 && v.p[0] === 0), 'all candidate paths are inside the POSTER group');
  const built = B.buildTagItems(scan.variants,
    { season: scan.seasonText, specials: [], cls: [], collection: [] }, 'Show (2020) {tmdb-1}');
  ok(built.items.length === 2, 'show(+poster alias dedup) + s1 → 2 items (got ' + built.items.length + ')');
  ok(built.items.every((it) => !it.changes.some((c) => c.p.length === 1 && c.p[0] === 0 && c.v === false)),
    'no item ever hides the POSTER group itself');

  // No ROOT-level POSTER group → whole-tree scan; a NESTED group named "poster" is never a candidate.
  const doc2 = { name: 'X.psd', layers: [grp('ART', [grp('poster', [lyr('art', true)], true)], true), lyr('s1')] };
  const scan2 = M.scanBatch(doc2, constants, '');
  ok(scan2.variants.length === 1 && scan2.variants[0].nm === 's1', 'whole-tree fallback skips the "poster" alias');
}

// ---- batch: season range from one poster ----
{
  const { model } = M.readModel(cl2k, constants);
  const r1 = B.parseRange('2');
  ok(r1.start === 1 && r1.end === 2, 'parseRange("2") = 1..2');
  const items1 = B.seasonItems(model, r1.start, r1.end);
  ok(items1.length === 2 && items1.every((it) => /Season [12]$/.test(it.suffix)), 'range "2" → Season 1 & 2 (no Specials, no S3)');

  const r2 = B.parseRange('0-3');
  const items2 = B.seasonItems(model, r2.start, r2.end);
  ok(items2.some((it) => it.suffix === ' - Specials'), 'range "0-3" includes Specials');
  ok(items2.filter((it) => /Season \d+$/.test(it.suffix)).length === 3, 'range "0-3" → 3 seasons');
}

// ---- geometry ----
{
  const fit = G.computePosterFitGeometry(1000, 1500, 1000, 1375);
  ok(fit.width === 950 && fit.left === 25 && fit.top === 25, 'Fit Poster: 2:3 src is width-driven → 950px, left 25, top 25');
  const sq = G.computePosterFitGeometry(1000, 1200, 1000, 1375);
  ok(sq.height === 1350 && sq.top + sq.height === 1375, 'Fit Poster: squarer src reaches the bottom guide');
  ok(sq.width === 1125 && sq.left === Math.floor((1000 - 1125) / 2), 'Fit Poster: side overhang stays centered');

  const logo = G.computeLogoGeometry(600, 200, 1000, 1500, 0.30);
  ok(logo.width > 0 && logo.height > 0, 'Place Logo: positive dimensions');
  ok(logo.top >= 0 && logo.top + logo.height <= 1500, 'Place Logo: sits within canvas height');
  ok(logo.left >= 0 && logo.left + logo.width <= 1000, 'Place Logo: sits within canvas width');
}

// ---- tags-mode filter (run only some tagged items, e.g. just the collection) ----
{
  ok(B.parseTagFilter('') === null, 'blank filter → run everything');
  const fk = B.parseTagFilter('c');
  ok(fk && fk.c === 1 && !fk.show, 'filter "c" selects only the collection key');
  const fk2 = B.parseTagFilter('main, s1-3');
  ok(fk2 && fk2.show === 1 && fk2.s1 === 1 && fk2.s3 === 1 && !fk2.c, 'filter "main, s1-3" expands correctly');
  ok(B.parseTagFilter('0').s0 === 1, 'filter "0" selects Specials');

  const built = B.buildTagItems(
    [{ nm: 'main', p: [0], v: true }, { nm: 's1', p: [1], v: true }, { nm: 'c', p: [2], v: true }],
    { season: [], specials: [], cls: [], collection: [] }, 'X');
  ok(built.items.every((it) => typeof it.key === 'string'), 'items carry their tag key');
  const only = built.items.filter((it) => B.parseTagFilter('c')[it.key]);
  ok(only.length === 1 && only[0].collection === true, 'filtering items by "c" leaves just the collection export');
}

console.log((fail === 0 ? 'PASS' : 'FAIL') + ' — ' + pass + ' checks passed, ' + fail + ' failed');
process.exit(fail === 0 ? 0 : 1);
