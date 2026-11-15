// Edit in tab / Send back, checked against a scripted Photoshop: the real tools.js runs, and a fake
// `photoshop` module answers its batchPlay and DOM calls and keeps a model of the open documents.
// It checks what the code asks Photoshop to do and in what order, not Photoshop itself. No Photoshop
// needed. Run from the repo root:
//   node photoshop-posterflow/test/tools-harness.js
'use strict';
const path = require('path');
const Module = require('module');
const root = path.join(__dirname, '..');

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('  FAIL: ' + m); } };

// ---- the scripted Photoshop ----
let nextId = 1;
let log = [];                  // what was asked of Photoshop, in order
let breakAt = '';              // 'move' | 'make' | 'trim' | 'crop' | 'list' | 'shift': that call fails, does nothing, or answers out of step
let kindReads = 0;             // how often a layer was asked what it is: each one is a round trip to Photoshop
let pixels = null;             // { clear: share of the tab that is see-through } | 'none' (no imaging) | 'throws'
const app = { documents: [], activeDocument: null };
const containerOf = (L) => (L.parent ? L.parent.layers : L._doc.layers);
const detach = (L) => { const c = containerOf(L); c.splice(c.indexOf(L), 1); };
const everyLayer = (doc) => { const out = []; const walk = (ls) => ls.forEach((L) => { out.push(L); if (L.layers) walk(L.layers); }); walk(doc.layers); return out; };
function layer(doc, props, parent) {
  const L = Object.assign({ id: nextId++, visible: true, opacity: 100, blendMode: 'normal', bounds: { left: 0, top: 0, right: doc.width, bottom: doc.height } }, props, { _doc: doc, parent: parent || null });
  const kind = (props && props.kind) || 'pixel';
  Object.defineProperty(L, 'kind', { enumerable: true, get() { kindReads++; return kind; } });
  L.move = async (rel, placement) => {
    if (breakAt === 'move') throw new Error('Photoshop refused the move');
    log.push('move ' + L.name + ' ' + placement + ' ' + rel.name);
    detach(L);
    if (placement === 'inside') { L.parent = rel; rel.layers.unshift(L); } else { L.parent = rel.parent; const c = containerOf(rel); c.splice(c.indexOf(rel), 0, L); }
  };
  L.translate = async (dx, dy) => { log.push('translate ' + L.name + ' ' + Math.round(dx) + ',' + Math.round(dy)); const b = L.bounds; L.bounds = { left: b.left + dx, top: b.top + dy, right: b.right + dx, bottom: b.bottom + dy }; };
  L.scale = async (sx) => { log.push('scale ' + L.name + ' ' + Math.round(sx)); const b = L.bounds, cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2, w = (b.right - b.left) * sx / 100, h = (b.bottom - b.top) * sx / 100; L.bounds = { left: cx - w / 2, top: cy - h / 2, right: cx + w / 2, bottom: cy + h / 2 }; };
  return L;
}
function makeDocument(name, width, height) {
  const doc = { id: nextId++, name, width, height, layers: [], activeLayers: [], guides: [{ direction: 'h', coordinate: 1375 }] };
  doc.crop = async (r) => { if (breakAt === 'crop') throw new Error('Photoshop could not crop'); log.push('crop ' + doc.name + ' ' + [r.left, r.top, r.right, r.bottom].join(',')); doc.width = r.right - r.left; doc.height = r.bottom - r.top; };
  doc.closeWithoutSaving = async () => { log.push('close ' + doc.name); app.documents = app.documents.filter((d) => d !== doc); };
  // Image > Duplicate: a copy of the whole document, opened as a tab of its own
  doc.duplicate = async () => {
    const dup = makeDocument(doc.name + ' copy', doc.width, doc.height);
    const copy = (ls, parent) => ls.map((L) => { const c = layer(dup, { name: L.name, kind: L.kind, visible: L.visible, bounds: L.bounds }, parent); if (L.layers) c.layers = copy(L.layers, c); return c; });
    dup.layers = copy(doc.layers, null);
    app.activeDocument = dup;
    log.push('duplicate ' + doc.name);
    return dup;
  };
  doc.trim = async () => { if (breakAt === 'trim') throw new Error('Photoshop could not trim'); log.push('trim ' + doc.name); };
  app.documents.push(doc);
  return doc;
}
const docById = (id) => app.documents.find((d) => d.id === id);
// The whole layer list in one answer, as multiGet gives it: bottom to top, a group as a start marker,
// its layers, then an end marker.
function layerList(doc) {
  const flat = [];
  const walk = (ls) => ls.forEach((L) => {
    const isGroup = !!L.layers;
    flat.push({ layerID: L.id, name: L.name, visible: L.visible, layerSection: { _value: isGroup ? 'layerSectionStart' : 'layerSectionContent' } });
    if (isGroup) { walk(L.layers); flat.push({ layerID: -1, name: '</Layer group>', visible: true, layerSection: { _value: 'layerSectionEnd' } }); }
  });
  walk(doc.layers);
  if (breakAt === 'shift') flat.unshift({ layerID: 9999, name: 'ghost', visible: true, layerSection: { _value: 'layerSectionContent' } });
  return flat.reverse();
}
async function batchPlay(descs) {
  const out = [];
  for (const d of descs) {
    if (d._obj === 'multiGet') {
      if (breakAt === 'list') throw new Error('multiGet is not available');
      const of = docById(d._target._ref[0]._id);
      log.push('layer list ' + of.name);
      out.push({ list: layerList(of) });
      continue;
    }
    const doc = app.activeDocument, t = d._target || [];
    if (d._obj === 'select') {
      const want = t[0], L = everyLayer(doc).find((x) => (want._id != null ? x.id === want._id : x.name === want._name));
      if (!L) throw new Error('no such layer');
      doc.activeLayers = [L];
    } else if (d._obj === 'make') {   // Layer > Duplicate Layer > New document
      if (breakAt === 'make') continue;
      const from = doc.activeLayers[0], tab = makeDocument(d.name, doc.width, doc.height);
      tab.layers.push(layer(tab, { name: from.name, visible: from.visible, bounds: from.bounds }));
      app.activeDocument = tab;
      log.push('new tab ' + d.name);
    } else if (d._obj === 'revealAll') {
      log.push('reveal all');
    } else if (d._obj === 'mergeVisible') {   // Stamp Visible
      if (doc.layers.length < 2) throw new Error('Stamp Visible is not available');
      const L = layer(doc, { name: 'stamp' }); doc.layers.unshift(L); doc.activeLayers = [L];
      log.push('stamp ' + doc.name);
    } else if (d._obj === 'duplicate') {      // copy the active layer into another document
      const to = docById(d.to._id), from = doc.activeLayers[0];
      to.layers.unshift(layer(to, { name: d.name, bounds: from.bounds }));
      log.push('copy ' + from.name + ' into ' + to.name);
    } else if (d._obj === 'delete') {
      const where = t[1] ? docById(t[1]._id) : doc;
      const L = everyLayer(where).find((x) => (t[0]._id != null ? x.id === t[0]._id : x.name === t[0]._name));
      if (!L) throw new Error('no such layer');
      detach(L);
      log.push('delete ' + L.name + ' from ' + where.name);
    } else if (d._obj === 'get') {
      const where = docById(t[2]._id), L = where && everyLayer(where).find((x) => x.id === t[1]._id);
      if (!L) throw new Error('no such layer');
      out.push({ layerID: L.id });
    } else throw new Error('scripted Photoshop: unexpected ' + d._obj);
  }
  return out;
}
// One history step: resumeHistory(step, false) puts the document back as it was when it was suspended.
const steps = [];
const snapshot = (doc) => everyLayer(doc).map((L) => ({ L, parent: L.parent, visible: L.visible, name: L.name, bounds: L.bounds })).concat([{ order: doc.layers.slice(), groups: everyLayer(doc).filter((L) => L.layers).map((G) => [G, G.layers.slice()]) }]);
const restore = (doc, snap) => {
  const last = snap[snap.length - 1];
  doc.layers = last.order.slice();
  last.groups.forEach(([G, kids]) => { G.layers = kids.slice(); });
  snap.slice(0, -1).forEach((s) => { s.L.parent = s.parent; s.L.visible = s.visible; s.L.name = s.name; s.L.bounds = s.bounds; });
};
const hostControl = {
  suspendHistory: async ({ documentID, name }) => { const doc = docById(documentID); steps.push({ doc, name, snap: snapshot(doc) }); return steps.length - 1; },
  resumeHistory: async (id, commit) => { const st = steps[id]; log.push('history "' + st.name + '" ' + (commit ? 'kept' : 'ROLLED BACK')); if (!commit) restore(st.doc, st.snap); },
};
const photoshop = {
  app, action: { batchPlay },
  core: { executeAsModal: async (fn) => fn({ hostControl }) },
  get imaging() {
    if (pixels === 'none') return undefined;
    return { getPixels: async (o) => {
      if (pixels === 'throws') throw new Error('no pixels');
      const data = new Uint8Array(10 * 10 * 4).fill(255);   // a scaled-down answer, as Photoshop may give
      for (let i = 0; i < Math.round(pixels.clear * 100); i++) data[i * 4 + 3] = 0;
      return { sourceBounds: o.sourceBounds, imageData: { width: 10, height: 10, components: 4, hasAlpha: true, getData: async () => data, dispose() {} } };
    } };
  },
};
// The maker's disk for fs.js: remembered folders by token, and what the picker hands back.
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const disk = {
  picks: [], gone: new Set(), pickerOpened: 0,
  getFolder: async () => { disk.pickerOpened++; return disk.picks.length ? disk.picks.shift() : null; },
  createPersistentToken: async (folder) => 'token:' + folder.name,
  getEntryForPersistentToken: async (tok) => ({ name: tok.slice(6), getMetadata: async () => { if (disk.gone.has(tok.slice(6))) throw new Error('gone'); } }),
};
const origLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'photoshop') return photoshop;
  if (request === 'uxp') return { storage: { localFileSystem: disk, formats: { binary: 'b' } } };
  return origLoad.call(this, request, ...rest);
};
const TL = require(path.join(root, 'tools.js'));
const G = require(path.join(root, 'geometry.js'));
const constants = { LayerKind: { GROUP: 'group' }, ElementPlacement: { PLACEBEFORE: 'before', PLACEINSIDE: 'inside' }, AnchorPosition: { MIDDLECENTER: 'mc' }, Direction: { HORIZONTAL: 'h' }, TrimType: { TRANSPARENT: 't' } };

// A poster PSD: LOGO (one visible logo) and POSTER (main on screen, s1 hidden). opts.noLogo leaves LOGO out.
function open(opts = {}) {
  app.documents = []; log = []; breakAt = ''; pixels = { clear: 0 }; steps.length = 0;
  const psd = makeDocument('Show (2020).psd', 1000, 1500);
  const logo = layer(psd, { name: 'LOGO', kind: 'group', layers: [] });
  logo.layers.push(layer(psd, { name: 'old logo', bounds: { left: 300, top: 1000, right: 700, bottom: 1150 } }, logo));
  const poster = layer(psd, { name: 'POSTER', kind: 'group', layers: [] });
  const main = layer(psd, { name: 'main', opacity: 80, blendMode: 'multiply', bounds: { left: -100, top: 25, right: 1100, bottom: 1450 } }, poster);
  const s1 = layer(psd, { name: 's1', visible: false, bounds: { left: -50, top: 25, right: 1050, bottom: 1400 } }, poster);
  poster.layers.push(main, s1);
  psd.layers = opts.noLogo ? [poster] : [logo, poster];
  if (opts.nested) {   // LOGO tucked inside another group
    const art = layer(psd, { name: 'ART', kind: 'group', layers: [logo] });
    logo.parent = art;
    psd.layers = [art, poster];
  }
  if (opts.seasons) {   // a big group above everything, as a template's SEASONS is
    const seasons = layer(psd, { name: 'SEASONS', kind: 'group', layers: [] });
    for (let n = 1; n <= opts.seasons; n++) seasons.layers.push(layer(psd, { name: 'Season ' + n, visible: false }, seasons));
    psd.layers.unshift(seasons);
  }
  app.activeDocument = psd;
  return { psd, logo, poster, main, s1 };
}
const ctxFor = (psd, L, kind) => ({ srcDocId: psd.id, layerId: L.id, layer: L, kind, tag: L.name, logoName: 'Show (2020) - Logo',
  left: L.bounds.left, top: L.bounds.top, w: L.bounds.right - L.bounds.left, h: L.bounds.bottom - L.bounds.top });
const names = (ls) => ls.map((L) => L.name).join(',');
// Edit a layer, then (as the maker would) add a retouch layer in the tab so there is something to stamp.
async function edit(L, kind, opts = {}) {
  const psd = L._doc, ctx = ctxFor(psd, L, kind);
  const tab = await TL.openLayerInTab(psd, L, L.name + ' · Show (2020)');
  if (!opts.untouched) tab.layers.unshift(layer(tab, { name: 'retouch' }));
  if (opts.resize) { tab.width = opts.resize[0]; tab.height = opts.resize[1]; }
  log = [];
  return { tab, ctx };
}

(async () => {
  // ---- Edit ----
  let d = open();
  let tab = await TL.openLayerInTab(d.psd, d.s1, 's1 · Show (2020)');
  ok(tab !== d.psd && app.activeDocument === tab && tab.name === 's1 · Show (2020)', 'Edit opens a new tab and leaves it in front');
  ok(tab.layers.length === 1 && tab.layers[0].visible === true, 'a hidden layer is shown in its tab');
  ok(log.join(' | ') === 'new tab s1 · Show (2020) | reveal all | crop s1 · Show (2020) -50,25,1050,1400', 'the tab is revealed and cropped to the layer\'s own pixels, overhang included: ' + log.join(' | '));
  ok(d.psd.width === 1000 && d.s1.visible === false && names(d.poster.layers) === 'main,s1', 'the PSD is untouched');

  d = open(); breakAt = 'make';
  let threw = '';
  try { await TL.openLayerInTab(d.psd, d.main, 'x'); } catch (e) { threw = e.message; }
  ok(threw === 'Photoshop did not open a new tab' && !log.some((l) => /^crop|reveal/.test(l)) && d.psd.width === 1000, 'if no tab opens, nothing is revealed or cropped (the PSD is never cropped): ' + threw + ' / ' + log.join(' | '));

  d = open(); breakAt = 'crop';
  threw = '';
  try { await TL.openLayerInTab(d.psd, d.main, 'main · Show (2020)'); } catch (e) { threw = e.message; }
  ok(threw === 'Photoshop could not crop' && app.documents.length === 1 && app.activeDocument === d.psd && d.psd.width === 1000,
    'if the new tab cannot be cropped, it is closed again and the PSD is back in front: ' + threw + ' / ' + app.documents.map((x) => x.name).join(', '));
  breakAt = '';

  // ---- Send back: a cleaned poster, same size ----
  d = open();
  let e = await edit(d.main, 'poster');
  let r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(r.ok && r.mode === 'poster' && r.refit === false, 'a cleaned poster goes back as the poster: ' + JSON.stringify(r));
  let back = d.poster.layers[0];
  ok(names(d.poster.layers) === 'main,s1' && back !== d.main, 'the new layer takes the old one\'s place in the stack, and the old one is gone: ' + names(d.poster.layers));
  ok(back.opacity === 80 && back.blendMode === 'multiply' && back.visible === true, 'it keeps the opacity, blend mode and visibility');
  ok(back.bounds.left === -100 && back.bounds.top === 25 && !log.some((l) => /^scale/.test(l)), 'the same size lands on the exact spot, not resampled: ' + JSON.stringify(back.bounds));
  ok(log.indexOf('history "Send back main" kept') >= 0 && log[log.length - 1] === 'close main · Show (2020)' && app.activeDocument === d.psd, 'one history step, then the tab closes and the PSD is in front: ' + log.join(' | '));
  ok(log[0] === 'stamp main · Show (2020)' && log[1] === 'copy stamp into Show (2020).psd', 'what the tab shows is stamped, then copied into the PSD');

  // a hidden layer stays hidden
  d = open();
  e = await edit(d.s1, 'poster');
  await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(d.poster.layers[1].name === 's1' && d.poster.layers[1].visible === false, 'a hidden season layer comes back hidden');

  // a tab left as one layer cannot be stamped: that layer goes back
  d = open();
  e = await edit(d.main, 'poster', { untouched: true });
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(r.ok && !log.some((l) => /^stamp/.test(l)) && log[0] === 'copy main into Show (2020).psd', 'a single-layer tab sends that layer back: ' + log.join(' | '));

  // ---- a poster whose size changed is fitted like Fit Poster ----
  d = open();
  e = await edit(d.main, 'poster', { resize: [600, 900] });   // the maker cropped the tab: the stamp is smaller than the original
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  const fit = G.computePosterFitGeometry(600, 900, 1000, 1375);
  back = d.poster.layers[0];
  ok(r.refit === true && Math.abs(back.bounds.left - fit.left) < 1 && Math.abs(back.bounds.top - fit.top) < 1 && Math.abs((back.bounds.right - back.bounds.left) - fit.width) < 1, 'a resized poster is cover-fitted to the template: ' + JSON.stringify(back.bounds) + ' vs ' + JSON.stringify(fit));

  // ---- a poster cut down to its lettering becomes a new logo ----
  d = open(); e = await edit(d.main, 'poster'); pixels = { clear: 0.85 };
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(r.ok && r.mode === 'newLogo' && Math.abs(r.share - 0.85) < 0.001 && r.hidOld === 1, 'a see-through tab is taken for a cut-out logo: ' + JSON.stringify(r));
  ok(names(d.logo.layers) === 'Show (2020) - Logo,old logo' && d.logo.layers[1].visible === false && d.logo.layers[0].visible === true, 'it becomes the top layer of LOGO and the earlier logo is hidden: ' + names(d.logo.layers));
  ok(names(d.poster.layers) === 'main,s1' && d.poster.layers[0] === d.main, 'the poster is left as it was');
  const lg = G.computeLogoGeometry(1200, 1425, 1000, 1500, TL.LOGO_DENSITY);   // the tab is the size of the poster layer
  ok(Math.abs((d.logo.layers[0].bounds.right - d.logo.layers[0].bounds.left) - lg.width) < 1 && d.psd.activeLayers[0] === d.main, 'it is placed like Place Logo, and the poster stays selected for the next Edit');
  ok(log.indexOf('history "Send back as logo" kept') >= 0, 'as one history step: ' + log.filter((l) => /^history/.test(l)));

  // just under the line it is still a poster; right on it, a logo
  d = open(); e = await edit(d.main, 'poster'); pixels = { clear: 0.01 };
  ok((await TL.sendBackFromTab(e.tab, e.ctx, constants)).mode === 'poster', '1% see-through is still a poster');
  d = open(); e = await edit(d.main, 'poster'); pixels = { clear: 0.02 };
  ok((await TL.sendBackFromTab(e.tab, e.ctx, constants)).mode === 'newLogo', '2% see-through is a logo');
  // when Photoshop cannot hand over the pixels, it stays a poster
  for (const how of ['none', 'throws']) {
    d = open(); e = await edit(d.main, 'poster'); pixels = how;
    ok((await TL.sendBackFromTab(e.tab, e.ctx, constants)).mode === 'poster', 'pixels unavailable (' + how + '): sent back as the poster');
  }

  // ---- an edited logo replaces its layer and is never measured ----
  d = open(); e = await edit(d.logo.layers[0], 'logo'); pixels = { clear: 0.9 };
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(r.ok && r.mode === 'logo' && r.refit === false && names(d.logo.layers) === 'old logo' && d.logo.layers[0].bounds.left === 300, 'a logo goes back in place of itself, on the same spot: ' + JSON.stringify(r) + ' ' + names(d.logo.layers));

  // ---- what cannot land leaves the tab open and the PSD as it was ----
  d = open({ noLogo: true }); e = await edit(d.main, 'poster'); pixels = { clear: 0.5 };
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(!r.ok && r.reason === 'no-logo-group' && app.documents.indexOf(e.tab) >= 0 && !log.some((l) => /^history|^copy/.test(l)), 'a cut-out logo with no LOGO group: refused before the PSD is touched: ' + JSON.stringify(r) + ' / ' + log.join(' | '));
  ok(names(e.tab.layers) === 'retouch,main', 'and the stamp is taken out of the tab again: ' + names(e.tab.layers));

  d = open(); e = await edit(d.main, 'poster');
  d.poster.layers.splice(0, 1);   // the maker deleted the layer meanwhile
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(!r.ok && r.reason === 'layer-gone' && app.documents.indexOf(e.tab) >= 0, 'the original layer is gone: refused, tab kept: ' + JSON.stringify(r));

  d = open(); e = await edit(d.main, 'poster');
  app.documents = app.documents.filter((x) => x !== d.psd);
  r = await TL.sendBackFromTab(e.tab, e.ctx, constants);
  ok(!r.ok && r.reason === 'psd-closed' && log.length === 0, 'the PSD is closed: refused before anything is done');

  d = open(); e = await edit(d.main, 'poster'); breakAt = 'move';
  threw = '';
  try { await TL.sendBackFromTab(e.tab, e.ctx, constants); } catch (x) { threw = x.message; }
  ok(threw === 'Photoshop refused the move' && log.indexOf('history "Send back main" ROLLED BACK') >= 0, 'a failure part-way rolls the history step back: ' + log.join(' | '));
  ok(names(d.poster.layers) === 'main,s1' && d.poster.layers[0] === d.main && names(d.psd.layers) === 'LOGO,POSTER', 'the PSD is exactly as it was: ' + names(d.psd.layers) + ' / ' + names(d.poster.layers));
  ok(app.documents.indexOf(e.tab) >= 0 && !log.some((l) => /^close/.test(l)), 'and the tab stays open');

  // ---- closing without sending back ----
  d = open(); e = await edit(d.main, 'poster');
  await TL.closeEditTab(e.tab, d.psd.id);
  ok(app.documents.indexOf(e.tab) < 0 && app.activeDocument === d.psd && names(d.poster.layers) === 'main,s1' && d.poster.layers[0] === d.main, 'X closes the tab and the PSD is untouched and in front');

  // ---- Save BG's last step: close the tab and take the backdrop layer out of the PSD ----
  d = open(); e = await edit(d.main, 'poster');
  let removed = await TL.closeEditTabAndRemove(e.tab, d.psd.id, d.main.id);
  ok(removed === true && names(d.poster.layers) === 's1' && app.documents.indexOf(e.tab) < 0 && app.activeDocument === d.psd, 'the tab closes, the PSD is in front and the backdrop layer is gone: ' + names(d.poster.layers));
  ok(log.join(' | ') === 'close main · Show (2020) | delete main from Show (2020).psd', 'in that order: ' + log.join(' | '));
  d = open(); e = await edit(d.main, 'poster');
  d.poster.layers.splice(0, 1);   // already deleted by hand
  removed = await TL.closeEditTabAndRemove(e.tab, d.psd.id, d.main.id);
  ok(removed === false && app.documents.indexOf(e.tab) < 0 && names(d.poster.layers) === 's1', 'a layer already gone is not an error, and nothing else is removed');
  d = open(); e = await edit(d.main, 'poster');
  app.documents = app.documents.filter((x) => x !== d.psd);
  removed = await TL.closeEditTabAndRemove(e.tab, d.psd.id, d.main.id);
  ok(removed === false && !log.some((l) => /^delete/.test(l)), 'with the PSD closed, the tab just closes');

  // ---- finding the LOGO group without walking the PSD layer by layer ----
  const lists = () => log.filter((l) => /^layer list/.test(l)).length;
  d = open({ seasons: 60 }); kindReads = 0; log = [];
  ok((await TL.findLogoGroup(d.psd, constants)) === d.logo && lists() === 0 && kindReads <= 3, 'LOGO at the top level: found from the top-level layers alone (' + kindReads + ' looks, ' + lists() + ' list calls)');
  d = open({ noLogo: true, seasons: 60 }); kindReads = 0; log = [];
  ok((await TL.findLogoGroup(d.psd, constants)) === null && lists() === 1 && kindReads <= 3, 'no LOGO at all (an MM2K template): one list call, and the 60 season layers are never walked (' + kindReads + ' looks)');
  d = open({ nested: true, seasons: 60 }); kindReads = 0; log = [];
  ok((await TL.findLogoGroup(d.psd, constants)) === d.logo && lists() === 1 && kindReads <= 3, 'a LOGO inside another group is still found, from one list call (' + kindReads + ' looks)');
  d = open({ nested: true, seasons: 60 }); kindReads = 0; log = []; breakAt = 'list';
  ok((await TL.findLogoGroup(d.psd, constants)) === d.logo && kindReads > 60, 'if the list call fails, the layers are walked as before and LOGO is still found (' + kindReads + ' looks)');
  breakAt = '';
  d = open({ nested: true, seasons: 60 }); kindReads = 0; log = []; breakAt = 'shift';
  ok((await TL.findLogoGroup(d.psd, constants)) === d.logo && kindReads > 60, 'if the list does not line up with the document, the wrong layer is never taken: the layers are walked and LOGO is found (' + kindReads + ' looks)');
  breakAt = '';
  // the batch's own LOGO steps use the same lookup
  d = open({ noLogo: true, seasons: 60 }); kindReads = 0; log = [];
  let placedLogo = await TL.placeCollectionLogo(d.psd, constants);
  ok(placedLogo.placed === false && /^No LOGO group/.test(placedLogo.warning) && kindReads <= 3, 'a collection export with no LOGO says so without walking the PSD (' + kindReads + ' looks)');
  kindReads = 0;
  await TL.hideManualCollectionLogos(d.psd, constants);
  ok(kindReads <= 3, 'the start of every batch no longer walks a template that has no LOGO (' + kindReads + ' looks)');

  // ---- Square Art: a crop that fails to open leaves no stray copy of the PSD behind ----
  d = open();
  let crop = await TL.makeCropDoc(d.psd, constants);
  ok(crop.ok && app.documents.indexOf(crop.doc) >= 0 && log.join(' | ') === 'duplicate Show (2020).psd | trim Show (2020).psd copy', 'a crop that opens stays open for the maker: ' + log.join(' | '));
  d = open();
  d.psd.layers = d.psd.layers.filter((L) => L.name !== 'POSTER');
  crop = await TL.makeCropDoc(d.psd, constants);
  ok(!crop.ok && crop.reason === 'noposter' && app.documents.length === 1 && app.documents[0] === d.psd, 'no POSTER group: the copy is closed again');
  d = open(); breakAt = 'trim';
  threw = '';
  try { await TL.makeCropDoc(d.psd, constants); } catch (x) { threw = x.message; }
  ok(threw === 'Photoshop could not trim' && app.documents.length === 1 && app.documents[0] === d.psd && log[log.length - 1] === 'close Show (2020).psd copy', 'a failure part-way closes the copy and still reports the failure: ' + threw + ' / ' + log.join(' | '));

  // ---- fs.js: every folder is asked for by name before its picker opens ----
  const FS = require(path.join(root, 'fs.js'));
  let asked = [], answer = true, missing = [];
  FS.setAsker(async (what) => { asked.push(what.name + ' / ' + what.hint); return answer; });
  FS.setOnMissing((name) => missing.push(name));
  const folderName = (f) => (f ? f.name : null);

  disk.picks = [{ name: 'Logos' }];
  ok(folderName(await FS.getLogoFolder({})) === 'Logos' && asked.join() === 'logo folder / where logo PNGs go' && disk.pickerOpened === 1, 'a folder never picked is asked for by name, then picked: ' + asked);
  asked = [];
  ok(folderName(await FS.getLogoFolder({})) === 'Logos' && asked.length === 0 && disk.pickerOpened === 1, 'a remembered folder is used without asking or picking');

  answer = false;
  ok((await FS.getSquareartFolder({})) === null && asked.join() === 'Square Art folder / where Square Art crops go' && disk.pickerOpened === 1, 'Cancel on the bar gives no folder, and the picker never opens');
  answer = true; asked = [];
  ok((await FS.getSquareartFolder({})) === null && asked.length === 1 && disk.pickerOpened === 2, 'cancelling the picker itself gives no folder either');

  disk.picks = [{ name: 'CL2K images' }, { name: 'Other' }]; asked = [];
  await FS.getImageFolder('CL2K', {});
  ok(asked.join() === 'CL2K image folder / where exported poster JPGs go', 'the image folder is named with its style: ' + asked);
  asked = [];
  ok(folderName(await FS.getImageFolder('CL2K', { forcePick: true })) === 'Other' && asked.length === 1, 'a deliberate re-pick asks by name too');

  disk.picks = [{ name: 'Backgrounds' }, { name: 'Backgrounds 2' }]; asked = [];
  await FS.getBackgroundFolder({});
  disk.gone.add('Backgrounds'); asked = [];
  // a remembered folder that cannot be reached: the note says which, then the ask, then the picker
  const realNow = Date.now; Date.now = () => realNow() + 120000;   // past the once-a-minute check
  ok(folderName(await FS.getBackgroundFolder({})) === 'Backgrounds 2' && missing.join() === 'backgrounds folder' && asked.join() === 'backgrounds folder / where Save BG puts backgrounds', 'an unreachable folder is named, asked for and picked again: ' + missing + ' / ' + asked);
  Date.now = realNow;
  asked = []; disk.picks = [{ name: 'Posters' }];
  await FS.getPosterFolder({});
  ok(asked.join() === "poster folder / where the Poster button's JPGs go", 'the poster folder: ' + asked);

  console.log((fail ? 'FAIL' : 'PASS') + ' — ' + pass + ' checks passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log('CRASH', e); process.exit(1); });
