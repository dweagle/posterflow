// Node smoke test for the document-independent logic (no Photoshop needed).
// Mocks the photoshop `constants` + a layer tree and exercises readModel / toggles / activeSuffix.
// Run: node photoshop-posterflow/test/smoke.js
'use strict';

const T = require('../toggle');
const M = require('../model');
const B = require('../batch');
const G = require('../geometry');
const A = require('../altkey');

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

  apply(model, T.clearAll(model));
  ok(model.singles.every((s) => s.v === false) && model.seasons.every((n) => n.v === false), 'clearAll hides every single, season, decade and the SEASONS group');
  ok(M.activeSuffix(model) === '', 'clearAll → no suffix (plain movie / show poster)');
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

// ---- Alt-click tracking (altkey.js): read from the pointer press, one-shot, never sticks ----
{
  const alt = A.createAltTracker();
  ok(alt.consume({ altKey: false }) === false, 'plain click is not an Alt-click');
  ok(alt.consume({ altKey: true }) === true, 'a click carrying altKey is an Alt-click');
  ok(alt.consume({ altKey: false }) === false, 'altKey on one click does not carry over to the next');

  alt.press({ altKey: true });
  ok(alt.consume({ altKey: false }) === true, 'altKey on the pointer press + click without it (UXP on Windows) is an Alt-click');
  ok(alt.consume({ altKey: false }) === false, 'the next plain click is NOT an Alt-click (stuck-Alt regression)');

  alt.press({ altKey: true }); alt.press({ altKey: false });
  ok(alt.consume({ altKey: false }) === true, 'pointerdown + mousedown of one press: either carrying Alt counts');
  alt.press({ altKey: false });
  ok(alt.consume({ altKey: false }) === false, 'a press without Alt is a plain click');

  alt.press({ altKey: true }); alt.release();
  ok(alt.consume({ altKey: false }) === false, 'release() (document click after the handler) clears a stranded press');
  alt.press(undefined);
  ok(alt.consume(undefined) === false, 'missing events are tolerated');
}

// ---- one-call layer tree (model.js treeFromList): multiGet order + group markers -> the DOM-shaped tree ----
{
  const start = (id, name, visible) => ({ layerID: id, name, visible, layerSection: { _enum: 'layerSectionType', _value: 'layerSectionStart' } });
  const end = (id) => ({ layerID: id, name: '</Layer group>', visible: false, layerSection: { _enum: 'layerSectionType', _value: 'layerSectionEnd' } });
  const leaf = (id, name, visible) => ({ layerID: id, name, visible, layerSection: { _enum: 'layerSectionType', _value: 'layerSectionContent' } });
  // Bottom-to-top, as multiGet returns it: [GRADIENT: grad] under [SEASONS: [1990-1999: S1, S2]] under [LOGO: logo art]
  const list = [
    end(10), leaf(11, 'grad', true), start(12, 'GRADIENT', true),
    end(20), end(21), leaf(22, 'Season 2', false), leaf(23, 'Season 1', true), start(24, '1990-1999', true), start(25, 'SEASONS', false),
    end(30), leaf(31, 'logo art', true), start(32, 'LOGO', true),
  ];
  const tree = M.treeFromList(list, constants);
  ok(tree.length === 3 && tree.map((n) => n.name).join(',') === 'LOGO,SEASONS,GRADIENT', 'top-level order is top-to-bottom like doc.layers (got ' + tree.map((n) => n.name).join(',') + ')');
  ok(tree[0].kind === 'group' && tree[0].layers.length === 1 && tree[0].layers[0].name === 'logo art', 'LOGO group holds its one leaf');
  ok(tree[1].layers.length === 1 && tree[1].layers[0].kind === 'group' && tree[1].layers[0].layers.map((n) => n.name).join(',') === 'Season 1,Season 2', 'nested decade keeps child order');
  ok(tree[1].layers[0].layers[0].id === 23 && tree[1].visible === false && tree[1].layers[0].layers[0].visible === true, 'ids and raw visibility carry through');
  ok(!JSON.stringify(tree).includes('</Layer group>'), 'end markers never become nodes');
  const { model, style } = M.readModel({ layers: tree }, constants);
  ok(style === 'CL2K' && model.seasons.filter((n) => n.r === 'season').length === 2, 'readModel reads the rebuilt tree like a DOM doc');
  const one = M.treeFromList([leaf(1, 'solo', true)], constants);
  ok(one.length === 1 && one[0].kind === 'layer' && one[0].layers.length === 0, 'a flat single-layer doc');
  ok(M.treeFromList([], constants).length === 0, 'an empty list is an empty tree');
}

// ---- finish tag check (batch.js tagProblems): what an unattended SHOW batch refuses to run on ----
{
  const base = 'Show (2020) {tmdb-1}';
  const probs = (layers) => B.tagProblems(M.scanBatch({ name: base + '.psd', layers }, constants, B.normName(base)), base);
  const good = probs([grp('POSTER', [lyr('show', true), lyr('s1'), lyr('s2-3')], true), grp('SEASONS', [lyr('Season 1')])]);
  ok(good.length === 0, 'show + s1 + s2-3 passes clean (got: ' + good.join(' | ') + ')');
  const untagged = probs([grp('POSTER', [lyr('show', true), lyr('Layer 2')], true)]);
  ok(untagged.length === 1 && /"Layer 2" is not tagged/.test(untagged[0]), 'an untagged POSTER child is reported (scanBatch alone never sees it)');
  const dup = probs([grp('POSTER', [lyr('show', true), lyr('s1'), lyr('s1')], true)]);
  ok(dup.length === 1 && /2 layers are all tagged "s1"/.test(dup[0]), 'two layers both tagged s1 are reported (got: ' + dup.join(' | ') + ')');
  const overlap = probs([grp('POSTER', [lyr('show', true), lyr('s2'), lyr('s1-3')], true)]);
  ok(overlap.length === 1 && /"s2" \/ "s1-3"/.test(overlap[0]), 's2 alongside an s1-3 range is reported');
  const noShow = probs([grp('POSTER', [lyr('s1', true), lyr('s2')], true)]);
  ok(noShow.length === 1 && /no layer is tagged show/.test(noShow[0]), 'a POSTER group without a show/main layer is reported');
  ok(probs([grp('ART', [lyr('show', true)], true)])[0] === 'no POSTER group at the top level', 'no root POSTER group is reported');
  ok(probs([grp('POSTER', [], true)])[0] === 'the POSTER group is empty', 'an empty POSTER group is reported');
  const alias = probs([grp('POSTER', [lyr('Show (2020) {tmdb-1}', true), lyr('s1')], true)]);
  ok(alias.length === 0, 'the PSD-name alias counts as the show layer');
  const both = probs([grp('POSTER', [lyr('main', true), lyr('show')], true)]);
  ok(both.length === 1 && /"main" \/ "show"/.test(both[0]), 'main + show together is reported once');
}

// ---- Square Art edge keeper (geometry.js): slide an off-canvas square back, leave the rest alone ----
{
  const K = G.keepSquareInside;
  const sel = (l, t, r, b, cw = 958, ch = 1436) => ({ l, t, r, b, cw, ch });
  ok(K(sel(0, 100, 958, 1058)) === null, 'the default full-width square sits on the canvas: left alone');
  const m = K(sel(118, 189, 1076, 1147));   // measured in the panel: dragged right, still a 958 square
  ok(m && m.side === 958 && m.x === 0 && m.y === 189, 'measured off-right drag resolves to 958 @ 0,189 (keeps its y)');
  const lft = K(sel(-200, 189, 758, 1147));
  ok(lft && lft.side === 958 && lft.x === 0 && lft.y === 189, 'off the left pins to x=0');
  const top = K(sel(0, -50, 958, 908));
  ok(top && top.x === 0 && top.y === 0, 'off the top pins to y=0');
  const bot = K(sel(0, 600, 958, 1558));
  ok(bot && bot.x === 0 && bot.y === 1436 - 958, 'off the bottom pins to the bottom edge');
  const cor = K(sel(-30, -30, 928, 928));
  ok(cor && cor.x === 0 && cor.y === 0 && cor.side === 958, 'off a corner pins on both axes');
  ok(K(sel(129, 100, 829, 800)) === null, 'a 700 preset mid-canvas: left alone');
  const r7 = K(sel(400, 100, 1100, 800));
  ok(r7 && r7.side === 700 && r7.x === 258 && r7.y === 100, '700 off the right: x=258, y untouched');
  const l7 = K(sel(-90, 300, 610, 1000));
  ok(l7 && l7.side === 700 && l7.x === 0 && l7.y === 300, '700 off the left: x=0, y untouched');
  const b7 = K(sel(100, 900, 800, 1600));
  ok(b7 && b7.side === 700 && b7.x === 100 && b7.y === 736, '700 off the bottom: y=736, x untouched');
  ok(K(sel(50, 50, 650, 650)) === null, 'a freshly drawn 600 inside the canvas: left alone');
  ok(K(sel(-40, 100, 660, 500)) === null, 'a non-square marquee is left to Crop\'s snap even off-canvas');
}

// ---- strip: export metadata scrub ----
{
  const S = require('../strip');
  const seg = (marker, payload) => [0xFF, marker, (payload.length + 2) >> 8, (payload.length + 2) & 0xFF, ...payload];
  const str = (s) => Array.from(s, (c) => c.charCodeAt(0));
  const exif = seg(0xE1, [...str('Exif'), 0, 0, 1, 2, 3]);
  const xmp = seg(0xE1, str('http://ns.adobe.com/xap/1.0/\0<x/>'));
  const irb = seg(0xED, [...str('Photoshop 3.0'), 0]);
  const c2pa = seg(0xEB, str('JP\0\0'));
  const adobe = seg(0xEE, [...str('Adobe'), 0, 100, 0, 0, 0, 0, 1]);
  const dqt = seg(0xDB, [0, ...new Array(64).fill(16)]);
  const sos = [0xFF, 0xDA, 0, 8, 1, 1, 0, 0, 63, 0, 0xAB, 0xCD, 0xFF, 0xD9];
  const jpg = Uint8Array.from([0xFF, 0xD8, ...exif, ...xmp, ...irb, ...c2pa, ...adobe, ...dqt, ...sos]);
  const out = new Uint8Array(S.stripJpeg(jpg.buffer));
  const markers = [];
  for (let i = 2; i + 4 <= out.length && out[i] === 0xFF && out[i + 1] !== 0xDA; i += 2 + ((out[i + 2] << 8) | out[i + 3])) markers.push(out[i + 1].toString(16));
  ok(markers.join(',') === 'e0,ee,db', 'jpeg: APP1/APP11/APP13 dropped, JFIF added, Adobe + DQT kept (got ' + markers + ')');
  ok(out[0] === 0xFF && out[1] === 0xD8, 'jpeg: SOI kept');
  ok(Array.from(out.subarray(out.length - sos.length)).join(',') === sos.join(','), 'jpeg: scan data + EOI copied verbatim');
  ok(new Uint8Array(S.stripJpeg(out.buffer)).length === out.length, 'jpeg: scrubbing twice is a no-op (JFIF not duplicated)');
  ok(S.stripJpeg(Uint8Array.from([1, 2, 3]).buffer).byteLength === 3, 'jpeg: non-JPEG bytes returned untouched');
  const chunk = (type, data) => [data.length >>> 24, (data.length >> 16) & 255, (data.length >> 8) & 255, data.length & 255, ...str(type), ...data, 0, 0, 0, 0];
  const png = Uint8Array.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
    ...chunk('IHDR', new Array(13).fill(0)), ...chunk('iTXt', [1, 2, 3]), ...chunk('pHYs', new Array(9).fill(0)),
    ...chunk('tEXt', [4]), ...chunk('eXIf', [5]), ...chunk('IDAT', [6, 7]), ...chunk('IEND', [])]);
  const pout = new Uint8Array(S.stripPng(png.buffer));
  const types = [];
  for (let i = 8; i + 12 <= pout.length;) { const len = ((pout[i] << 24) | (pout[i + 1] << 16) | (pout[i + 2] << 8) | pout[i + 3]) >>> 0; types.push(String.fromCharCode(pout[i + 4], pout[i + 5], pout[i + 6], pout[i + 7])); i += 12 + len; }
  ok(types.join(',') === 'IHDR,pHYs,IDAT,IEND', 'png: text/exif chunks dropped, the rest kept in order (got ' + types + ')');
  ok(S.stripPng(Uint8Array.from([1, 2, 3]).buffer).byteLength === 3, 'png: non-PNG bytes returned untouched');
}

// ---- the three panels report the same version ----
{
  const fs = require('fs'), path = require('path');
  const ps = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'manifest.json'), 'utf8')).version;
  ['photopea-posterflow/photopea-posterflow.html', 'frontend/public/photopea-plugin.html'].forEach((f) => {
    const m = fs.readFileSync(path.join(__dirname, '..', '..', f), 'utf8').match(/PANEL_VERSION = '([^']+)'/);
    ok(!!m && m[1] === ps, f + ' PANEL_VERSION ' + (m ? m[1] : '(missing)') + ' should match the Photoshop manifest ' + ps);
  });
}

console.log((fail === 0 ? 'PASS' : 'FAIL') + ' — ' + pass + ' checks passed, ' + fail + ' failed');
process.exit(fail === 0 ? 0 : 1);
