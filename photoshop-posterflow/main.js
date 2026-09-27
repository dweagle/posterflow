// Panel controller. Replaces the Photopea panel's postMessage/echo plumbing with direct photoshop
// DOM calls. Every document mutation (visibility toggles, save, JPG, place, trim, batch) runs through
// one serialized modal queue (runExclusive → core.executeAsModal) because UXP forbids overlapping
// modal scopes.
'use strict';

const { app, core, constants, action } = require('photoshop');
const T = require('./toggle');
const M = require('./model');
const S = require('./save');
const B = require('./batch');
const G = require('./geometry');
const TL = require('./tools');
const FS = require('./fs');
const R = require('./remote');
const A = require('./altkey');

// ---- DOM refs ----
const styleEl   = document.querySelector('[data-el="style"]');
const singlesEl = document.querySelector('.singles');
const sequelsEl = document.querySelector('.sequels');
const listEl    = document.querySelector('.list');
const saveBtn   = document.querySelector('[data-act="save"]');
const jpgBtn    = document.querySelector('[data-act="jpg"]');
const logoBtn   = document.querySelector('[data-act="logo"]');
const fitBtn    = document.querySelector('[data-act="fit"]');
const trimBtn   = document.querySelector('[data-act="trim"]');
const logoExpBtn = document.querySelector('[data-act="logo-export"]');
const posterExpBtn = document.querySelector('[data-act="poster-export"]');
const squareArtBtn = document.querySelector('[data-act="squareart"]');
const sqCancelBtn  = document.querySelector('[data-act="sq-cancel"]');
const sqPresetsEl  = document.querySelector('.sqpresets');
const batchBtn    = document.querySelector('[data-act="batch"]');          // amber ⚡ — name-tag batch
const seasonsBtn  = document.querySelector('[data-act="batch-seasons"]');  // blue ⚡ — seasons from one poster
const batchBar    = document.querySelector('.batchbar');
const batchMsgEl  = document.querySelector('.batchmsg');
const batchRow    = document.querySelector('.batchrow');
const batchInput  = document.querySelector('.batchinput');
const batchHelp   = document.querySelector('.batchhelp');
// Gradient safety net opt-out — remembered across sessions, ON by default.
const gradChk = document.querySelector('[data-act="batch-grad"]');
try { gradChk.checked = localStorage.getItem('posterflow.batchGradient') !== '0'; } catch (_) { gradChk.checked = true; }
gradChk.addEventListener('change', () => { try { localStorage.setItem('posterflow.batchGradient', gradChk.checked ? '1' : '0'); } catch (_) {} });

// ---- state ----
let model = { singles: [], seasons: [], sequels: [], seqGroup: null };
let layerByPath = {};
let curStyle = '';
let curLogoGroup = null;   // path of the LOGO group in the active doc (null = none → Logo button hidden)
const remoteDocs = {};     // doc.id → { filename, style, name, entry } for docs opened from the Posterflow queue
const remoteCtx = () => (hasDoc() ? remoteDocs[app.activeDocument.id] : null) || null;
let batchMode = 'tags', batchConvention = false, batchItems = [], batchWarnings = [], batchBusy = false;
let sqArmed = false, sqBusy = false;      // Square Art: crop tab open / op in flight
let sqTemp = null, sqHome = null;         // { doc, width, height } crop doc + the PSD to return to
let sqCtx = null, sqWant = 0;             // remote ctx captured at start + last preset size

// Alt comes from the pointer press (widget click events drop altKey on Windows); read it once per click.
const alt = A.createAltTracker();
document.addEventListener('pointerdown', (e) => alt.press(e), true);
document.addEventListener('mousedown', (e) => alt.press(e), true);
document.addEventListener('click', () => alt.release());
const wantsRepick = (ev) => alt.consume(ev);

// ---- serialized modal queue: one document edit at a time, no dropped clicks ----
let chain = Promise.resolve();
let busy = 0;        // in-flight ops — auto-rescan stays quiet while we're the ones editing
let lastDocId = 0;   // active doc id at last scan — the poll refreshes on doc switch
function runExclusive(fn) {
  busy++;
  const next = chain.then(fn, fn);
  chain = next.catch(() => {});   // keep the chain alive even if one op rejects
  next.then(() => { busy--; }, () => { busy--; });
  return next;
}

// ---- small helpers ----
const NOTE_TTL = 15000;   // ms a note stays before clearing itself
const notesEl = document.querySelector('.notes');   // dedicated container — render() never wipes it
const note = (t) => {
  const d = document.createElement('div');
  d.className = 'msg'; d.textContent = t;
  notesEl.insertBefore(d, notesEl.firstChild);
  setTimeout(() => { if (d.parentNode) d.parentNode.removeChild(d); }, NOTE_TTL);
};
const showError = (e) => note('Error: ' + (e && e.message ? e.message : e));
const flash = (btn, mark, restore) => { btn.textContent = mark; setTimeout(() => { btn.textContent = restore; }, 1400); };
const hasDoc = () => app.documents && app.documents.length > 0;
const baseName = (doc) => (((doc || app.activeDocument) || {}).name || 'poster').replace(/\.(psd|psb|jpe?g|png|webp|gif|bmp|tiff?)$/i, '');
// Strip the {tmdb-…} {tvdb-…} {imdb-…} id tags: "Ragna Crimson (2023) {tmdb-1} {imdb-tt2}" -> "Ragna Crimson (2023)"
const cleanTitle = (s_) => String(s_ || '').replace(/\s*\{[^}]*\}/g, '').replace(/\s+/g, ' ').trim();

function updateBadge(style) {
  styleEl.textContent = style || '-';
  styleEl.className = 'style-name' + (style === 'MM2K' ? ' mm2k' : style === 'CL2K' ? ' cl2k' : '');
  // MM2K templates have text titles, no LOGO group → Place Logo is meaningless; hide it (Fit stays).
  logoBtn.classList.toggle('hidden', style === 'MM2K');
  logoExpBtn.classList.toggle('hidden', !curLogoGroup);   // Logo export needs an actual LOGO group
}

// ---- apply a changes list [{p,v}] to the real layers, then re-render ----
let selfEditUntil = 0;   // the auto-rescan ignores show/hide events from our own writes until then
let edits = 0;           // bumped per write; a refresh that read the document before a write drops its result
// Pair each change with its layer id (paths without a mapped layer are skipped).
const pairsFor = (changes) => changes.map((ch) => { const L = layerByPath[T.key(ch.p)]; return L && L.id !== undefined ? { id: L.id, v: ch.v } : null; }).filter(Boolean);
async function writeVisible(pairs, commandName) {
  edits++;
  try { await TL.setLayersVisible(pairs, commandName); }
  finally { selfEditUntil = Date.now() + 1000; }
}
function applyChangesCore(changes) {
  // Only write layers whose RAW visibility actually changes (rv): a season click "hides" dozens of
  // already-hidden layers. Paths without a model node (ancestor-group reveals, seqGroup) always apply.
  const nodes = T.allNodes(model);
  const toApply = changes.filter((ch) => {
    const k = T.key(ch.p);
    const n = nodes.find((m) => T.key(m.p) === k);
    return !n || n.rv !== ch.v;
  });
  // Optimistic: the chips show the click at once; Photoshop catches up behind the modal queue, and a
  // failed write re-reads the document rather than trusting the guess.
  changes.forEach((ch) => { const k = T.key(ch.p); nodes.forEach((n) => { if (T.key(n.p) === k) { n.v = ch.v; n.rv = ch.v; } }); });
  render();
  const pairs = pairsFor(toApply);
  if (!pairs.length) return Promise.resolve();
  return runExclusive(async () => {
    try { await writeVisible(pairs, 'Toggle poster layers'); }
    catch (e) { refresh(); throw e; }
  });
}
const applyChanges = (changes) => applyChangesCore(changes).catch(showError);

const onSeason = (S_) => applyChanges(T.clickSeason(model, S_));
const onSingle = (SI) => applyChanges(T.clickSingle(model, SI));
const onSequel = (SQ) => applyChanges(T.clickSequel(model, SQ));
const onMain   = (Mn) => applyChanges(T.clickMain(model, Mn));
const onDecade = (D)  => applyChanges(T.clickDecade(model, D));
const onYear   = (Y)  => applyChanges(T.clickYear(model, Y));
const onYearsG = (YG) => applyChanges(T.clickYearsGroup(model, YG));

// ---- Tagging: rename the selected layer to a Posterflow export tag ----
// The tag language is parseTagName() in batch.js (a port of frontend/src/lib/photopeaBatch.ts):
//   show / movie / poster / main -> the main export (no filename suffix)
//   s0 -> Specials, s{N} -> Season N (1-4 digits, so year seasons like s1985 are valid)
//   c -> Collection, cls -> Complete Limited Series (writes the Season 1 file)
// Chips that map to a tag rename on Alt-click. MOVIE / SHOW have no layer to toggle: a plain
// click clears every season / single layer, Alt-click renames.
const SINGLE_TAG = { SP: 's0', C: 'c', CLS: 'cls' };
const MAIN_TAGS = [{ lab: 'MOVIE', tag: 'main' }, { lab: 'SHOW', tag: 'show' }];

// Season chips ("3") and season-year chips ("1985") both become s{digits}.
// Group chips (S, S1-10, YEARS) have no tag in the language.
function chipTag(N) {
  if (N.r !== 'season' && N.r !== 'year') return null;
  const lab = M.label(N.n);
  return /^\d{1,4}$/.test(lab) ? 's' + parseInt(lab, 10) : null;
}

const tagTitle = (base, tag) =>
  base + '  \u00b7  Alt-click: rename the selected layer to "' + tag + '"';

async function renameSelectedTo(tag) {
  if (!hasDoc()) { note('No document open.'); return; }
  const doc = app.activeDocument;
  const sel = (doc.activeLayers || []);
  if (!sel.length) { note('Select a layer first, then tag it.'); return; }
  const layer = sel[0];
  const from = layer.name;
  if (from === tag) { note('That layer is already named "' + tag + '".'); return; }
  try {
    await runExclusive(() => core.executeAsModal(
      async () => { layer.name = tag; },
      { commandName: 'Rename layer to ' + tag }
    ));
    note('Renamed "' + from + '" \u2192 "' + tag + '"' +
         (sel.length > 1 ? '  (' + sel.length + ' selected; only the first was renamed)' : '') + '.');
  } catch (e) { showError(e); }
}

// ---- render ----
function chip(text, on, folder) {
  const c = document.createElement('div');
  c.className = 'chip ' + (on ? 'on' : 'off') + (folder ? ' grp' : '');
  c.textContent = text;
  return c;
}

// UXP renders `title` tooltips only on Spectrum widgets, so the div chips get their own hover tip:
// shown under the chip after a short delay, hidden on leave / press / scroll / re-render.
const tipEl = document.createElement('div');
tipEl.className = 'tip hidden';
document.body.appendChild(tipEl);
let tipTimer = null;
const hideTip = () => { if (tipTimer) { clearTimeout(tipTimer); tipTimer = null; } tipEl.classList.add('hidden'); };
function tip(el, text) {
  el.addEventListener('mouseover', () => {
    if (tipTimer) clearTimeout(tipTimer);
    tipTimer = setTimeout(() => {
      tipTimer = null;
      tipEl.textContent = text;
      tipEl.style.left = '0px'; tipEl.style.top = '0px';
      tipEl.classList.remove('hidden');
      const r = el.getBoundingClientRect(), t = tipEl.getBoundingClientRect();
      const bw = document.body.clientWidth, bh = document.body.clientHeight;
      const left = Math.max(2, Math.min(r.left, bw - t.width - 2));
      const top = (r.bottom + 4 + t.height <= bh) ? r.bottom + 4 : Math.max(2, r.top - t.height - 4);
      tipEl.style.left = Math.round(left) + 'px'; tipEl.style.top = Math.round(top) + 'px';
    }, 350);
  });
  el.addEventListener('mouseout', hideTip);
  el.addEventListener('mousedown', hideTip);
}
listEl.addEventListener('scroll', hideTip);

function render() {
  hideTip();
  singlesEl.innerHTML = '';
  model.singles.forEach((SI) => {
    const c = chip(SI.lab, SI.v, false);
    const tag = SINGLE_TAG[SI.lab] || null;
    tip(c, tag ? tagTitle(SI.n, tag) : SI.n);
    c.addEventListener('click', (ev) => {
      if (tag && wantsRepick(ev)) renameSelectedTo(tag); else onSingle(SI);
    });
    singlesEl.appendChild(c);
  });
  MAIN_TAGS.forEach((T_) => {
    const c = chip(T_.lab, false, false);
    c.classList.add('tagonly');
    tip(c, 'Clear all season / Specials / Collection / CLS layers.  \u00b7  '
           + 'Alt-click: rename the selected layer to "' + T_.tag + '"  \u00b7  ' + finishTip(T_.tag));
    c.addEventListener('click', (ev) => {
      if (wantsRepick(ev)) renameSelectedTo(T_.tag);
      else applyChanges(T.clearAll(model));
    });
    c.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();   // keep Photoshop's own menu out of it
      alt.release();         // a right-click fires no 'click', so drop whatever Alt its press recorded
      if (T_.tag === 'main') onFinishMovie(); else onFinishShow(ev.shiftKey ? 'seasons' : 'tags');
    });
    singlesEl.appendChild(c);
  });
  // MOVIE / SHOW are always available, so the row shows even with no SP/C/CLS layers.
  singlesEl.classList.remove('hidden');

  sequelsEl.innerHTML = '';
  const sqs = T.seqs(model);
  if (sqs.length) {
    const lbl = document.createElement('span'); lbl.className = 'rowlabel'; lbl.textContent = 'Sequel';
    sequelsEl.appendChild(lbl);
    sqs.forEach((SQ) => {
      const c = chip(M.seqLabel(SQ.n), SQ.v, false); c.classList.add('num'); tip(c, SQ.n);
      c.addEventListener('click', () => onSequel(SQ));
      sequelsEl.appendChild(c);
    });
  }
  sequelsEl.classList.toggle('hidden', sqs.length === 0);

  listEl.innerHTML = '';
  if (!model.singles.length && !model.seasons.length && !sqs.length) {
    listEl.innerHTML = '<div class="msg">No SEASONS / SPECIALS layers found. Press ⟳ once the PSD is open.</div>';
    return;
  }
  // With no decade or YEARS group open, still show the first decade's season chips
  // (unselected) so a layer can be Alt-click tagged without switching SEASONS on first.
  const anyOpen = model.seasons.some((n) => (n.r === 'decade' || n.r === 'years') && n.v);
  const firstDecade = model.seasons.filter((n) => n.r === 'decade')[0] || null;
  let decadeOpen = true, yearsOpen = true;
  let row = null, rowCount = 0, rowRole = '';   // season chips flow in centered rows of 5, year chips (wider) in rows of 4
  model.seasons.forEach((N) => {
    if (N.r === 'other' || N.r === 'group' || N.r === 'otherLeaf') return;
    if (N.r === 'decade') decadeOpen = N.v || (!anyOpen && N === firstDecade);
    if (N.r === 'years') yearsOpen = N.v;
    if (N.r === 'season' && !decadeOpen) return;
    if (N.r === 'year' && !yearsOpen) return;
    const folder = (N.r === 'main' || N.r === 'decade' || N.r === 'years');
    const c = chip(M.label(N.n), N.v, folder);
    const sTag = chipTag(N);
    tip(c, sTag ? tagTitle(N.n, sTag) : N.n);
    if (N.r === 'season')      { c.classList.add('num'); c.addEventListener('click', (ev) => { if (sTag && wantsRepick(ev)) renameSelectedTo(sTag); else onSeason(N); }); }
    else if (N.r === 'year')   c.addEventListener('click', (ev) => { if (sTag && wantsRepick(ev)) renameSelectedTo(sTag); else onYear(N); });
    else if (N.r === 'decade') c.addEventListener('click', () => onDecade(N));
    else if (N.r === 'years')  c.addEventListener('click', () => onYearsG(N));
    else                       c.addEventListener('click', () => onMain(N));
    if (N.r === 'season' || N.r === 'year') {
      const max = N.r === 'season' ? 5 : 4;
      if (!row || rowCount === max || rowRole !== N.r) { row = document.createElement('div'); row.className = 'chiprow'; listEl.appendChild(row); rowCount = 0; rowRole = N.r; }
      row.appendChild(c); rowCount++;
    } else {
      row = null; rowCount = 0; rowRole = '';
      listEl.appendChild(c);
    }
  });
}

// ---- read the active document into the model ----
let lastTree = null;     // what the model was read from: the one-call tree, or the DOM as a fallback
let treeReadOk = true;   // flips off (once, logged) if multiGet is unavailable
async function readSource(doc) {
  if (treeReadOk) {
    try { return { layers: await TL.readLayerTree(doc, constants) }; }
    catch (e) { treeReadOk = false; console.log('Posterflow: one-call layer read unavailable, walking the DOM instead.', e); }
  }
  return doc;
}
async function refresh() {
  if (!hasDoc()) {
    lastDocId = 0;
    model = { singles: [], seasons: [], sequels: [], seqGroup: null }; layerByPath = {}; curStyle = ''; curLogoGroup = null; lastTree = null;
    updateBadge(''); singlesEl.classList.add('hidden'); sequelsEl.classList.add('hidden');
    listEl.innerHTML = '<div class="msg">Open a poster PSD, then press ⟳.</div>';
    return;
  }
  try {
    const doc = app.activeDocument, e0 = edits;
    const src = await readSource(doc);
    if (!hasDoc() || app.activeDocument.id !== doc.id) return;   // switched documents meanwhile; the poll follows up
    if (edits !== e0) return;                                   // a click landed during the read: the model already knows more
    lastDocId = doc.id; lastTree = src;
    const r = M.readModel(src, constants);
    model = r.model; layerByPath = r.layerByPath; curStyle = r.style; curLogoGroup = r.logoGroup;
    const ORD = { SP: 0, C: 1, CLS: 2 };
    model.singles.sort((a, b) => ORD[a.lab] - ORD[b.lab]);
    model.sequels.sort((a, b) => (parseInt(M.seqLabel(a.n), 10) || 0) - (parseInt(M.seqLabel(b.n), 10) || 0));
    updateBadge(curStyle);
    render();
  } catch (e) { showError(e); }
}

// ---- Save PSD (remote docs PUT back to the server; local docs overwrite the opened file) ----
async function saveCore() {   // throws on failure; returns the remote filename, or null for a local doc
  const ctx = remoteCtx();
  if (ctx) await runExclusive(() => S.savePsdRemote(app.activeDocument, ctx));
  else await runExclusive(() => S.savePsd(app.activeDocument));
  return ctx ? ctx.filename : null;
}
async function onSave() {
  if (!hasDoc()) { note('No document open.'); return; }
  saveBtn.textContent = '…';
  try {
    const remote = await saveCore();
    if (remote) note('Saved "' + remote + '" back to Posterflow.');
    flash(saveBtn, '✓', '💾');
  } catch (e) { flash(saveBtn, '✗', '💾'); showError(e); }
}

// ---- Export JPG (remote docs upload to the server's image folder; local docs use the picked folder) ----
async function jpgCore(repick) {   // { ok, filename, folderName } or { ok: false, reason, msg }
  const ctx = remoteCtx();
  const base = (ctx && ctx.name) || baseName();
  let res;
  if (ctx) {
    // Server upload first; a 400 means no image folder is configured there — fall back to the
    // panel's own remembered local folder (prompting once), so a blank server path never errors.
    try {
      res = await runExclusive(() => S.exportJpgRemote(app.activeDocument, ctx, base, M.activeSuffix(model)));
    } catch (e) {
      if (!/HTTP 400/.test(String(e && e.message))) throw e;
      note('Server has no image export folder; saving locally instead.');
      res = await runExclusive(() => S.exportJpg(app.activeDocument, ctx.style, base, M.activeSuffix(model), { forcePick: repick }));
    }
  } else {
    res = await runExclusive(() => S.exportJpg(app.activeDocument, curStyle, base, M.activeSuffix(model), { forcePick: repick }));
  }
  if (!res.ok) res.msg = res.reason === 'cancelled' ? 'no folder chosen' : 'export failed';
  return res;
}
async function onJpg(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  const repick = wantsRepick(ev);
  jpgBtn.textContent = '…';
  try {
    const res = await jpgCore(repick);
    if (res.ok) { flash(jpgBtn, '✓', 'JPG'); note('Saved "' + res.filename + '" → ' + res.folderName); }
    else { jpgBtn.textContent = 'JPG'; if (res.reason === 'cancelled') note('JPG cancelled: no folder chosen.'); }
  } catch (e) { flash(jpgBtn, '✗', 'JPG'); showError(e); }
}

// ---- Place Logo / Fit Poster (the SELECTED layer) ----
async function onPlace(mode, btn, label) {
  if (!hasDoc()) { note('No document open.'); return; }
  btn.textContent = '…';
  try {
    const res = await runExclusive(() => TL.placeSelected(app.activeDocument, mode, constants));
    if (res.ok) flash(btn, '✓', label);
    else { btn.textContent = label; note('Select a layer first, then press ' + label + '.'); }
  } catch (e) { flash(btn, '✗', label); showError(e); }
}

// ---- Alt-click Logo: rename the visible layer inside the LOGO group to "<title> - Logo" ----
// Find the LOGO group LIVE (like tools.js exportLogoPng) — the cached layerByPath snapshot can go stale.
function findLogoGroupLive(layers) {
  for (let i = 0; i < layers.length; i++) {
    const L = layers[i];
    if (L.kind === constants.LayerKind.GROUP) {
      if (/^\s*logos?\s*$/i.test(L.name)) return L;
      const f = findLogoGroupLive(L.layers);
      if (f) return f;
    }
  }
  return null;
}
const logoLayerName = () => { const ctx = remoteCtx(); return cleanTitle((ctx && ctx.name) || baseName()) + ' - Logo'; };
// Rename the visible layer of `grp` (the topmost when several) to `target`; `one` refuses several visible.
async function renameVisibleIn(grp, groupLabel, target, one) {
  const kids = Array.from(grp.layers || []);
  if (!kids.length) throw new Error('The ' + groupLabel + ' group is empty.');
  const vis = kids.filter((L) => L.visible);
  if (!vis.length) throw new Error('No visible layer in the ' + groupLabel + ' group.');
  if (one && vis.length > 1) throw new Error(vis.length + ' ' + groupLabel + ' layers are visible; leave one on.');
  const layer = vis[0], from = layer.name;
  if (from !== target) {
    await runExclusive(() => core.executeAsModal(async () => { layer.name = target; }, { commandName: 'Rename ' + groupLabel + ' layer' }));
  }
  return { from, to: target, same: from === target, vis: vis.length };
}
async function renameLogoLayer() {
  if (!hasDoc()) { note('No document open.'); return; }
  const grp = findLogoGroupLive(app.activeDocument.layers);
  if (!grp) { note('No LOGO group in this document.'); return; }
  try {
    const r = await renameVisibleIn(grp, 'LOGO', logoLayerName(), false);
    if (r.same) note('The logo layer is already named "' + r.to + '".');
    else note('Renamed "' + r.from + '" \u2192 "' + r.to + '"' + (r.vis > 1 ? '  (' + r.vis + ' visible; only the topmost was renamed)' : '') + '.');
  } catch (e) { note(e && e.message ? e.message : String(e)); }
}

// ---- Export the LOGO group as a trimmed transparent PNG (Alt-click re-picks the folder).
// Remote docs render to a temp file and upload to the server's logo folder instead. ----
async function logoCore() {   // { ok, filename, where } or { ok: false, reason, msg }
  const ctx = remoteCtx();
  const folder = ctx ? await R.tempFolder() : await FS.getLogoFolder({});
  if (!folder) return { ok: false, reason: 'cancelled', msg: 'no folder chosen' };
  const filename = ((ctx && ctx.name) || baseName()) + ' - logo.png';
  const res = await runExclusive(() => TL.exportLogoPng(app.activeDocument, constants, folder, filename));
  if (!res.ok) {
    return { ok: false, reason: res.reason, msg: res.reason === 'no-logo' ? 'no LOGO group' : res.reason === 'empty' ? 'LOGO group has no visible pixels' : 'export failed' };
  }
  if (!ctx) return { ok: true, filename: res.filename, where: res.folderName };
  const bytes = await R.readBytes(res.entry);
  try {
    await R.putBytes('/api/maker-tools/logo-exports/' + encodeURIComponent(res.filename), bytes);
    return { ok: true, filename: res.filename, where: 'Posterflow logo folder' };
  } catch (e) {
    if (!/HTTP 400/.test(String(e && e.message))) throw e;
    // No logo folder configured server-side — fall back to the panel's local logo folder.
    note('Server has no logo export folder; saving locally instead.');
    const localFolder = await FS.getLogoFolder({});
    if (!localFolder) return { ok: false, reason: 'cancelled', msg: 'no folder chosen' };
    await FS.writeFileBytes(localFolder, res.filename, bytes);
    return { ok: true, filename: res.filename, where: localFolder.name };
  }
}
async function onLogoExport(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  if (wantsRepick(ev)) { await renameLogoLayer(); return; }
  if (!curLogoGroup) { note('No LOGO group in this document.'); return; }
  logoExpBtn.textContent = '…';
  try {
    const res = await logoCore();
    if (res.ok) { flash(logoExpBtn, '✓', 'Logo'); note('Saved "' + res.filename + '" → ' + res.where); }
    else if (res.reason === 'cancelled') { logoExpBtn.textContent = 'Logo'; note('Logo export cancelled: no folder chosen.'); }
    else {
      flash(logoExpBtn, '✗', 'Logo');
      note(res.reason === 'no-logo' ? 'No LOGO group in this document.' : res.reason === 'empty' ? 'LOGO group has no visible pixels.' : 'Logo export failed.');
    }
  } catch (e) { flash(logoExpBtn, '✗', 'Logo'); showError(e); }
}

// ---- Trim off-canvas pixels ----
async function onTrim() {
  if (!hasDoc()) { note('No document open.'); return; }
  trimBtn.textContent = '…';
  try { await runExclusive(() => TL.trim(app.activeDocument)); flash(trimBtn, '✓', 'Trim'); }
  catch (e) { flash(trimBtn, '✗', 'Trim'); showError(e); }
}

// ---- Poster export ----
// Export the SELECTED layer(s) alone as trimmed JPGs, named by tag: s1/s2… → " - Season N",
// s0 → " - Specials", anything else → plain. Alt-click re-picks the folder; remote docs upload.
const pexSuffix = (nm) => {
  const m = ('' + nm).trim().toLowerCase().match(/^s0*(\d+)$/);
  if (!m) return '';
  const n = parseInt(m[1], 10);
  return n === 0 ? ' - Specials' : ' - Season ' + n;
};
async function onPosterExport(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  const repick = wantsRepick(ev);
  const ctx = remoteCtx();
  posterExpBtn.textContent = '…';
  try {
    const folder = ctx ? await R.tempFolder() : await FS.getPosterFolder({ forcePick: repick });
    if (!folder) { posterExpBtn.textContent = 'Poster'; note('Poster export cancelled: no folder chosen.'); return; }
    const base = (ctx && ctx.name) || baseName();
    const n = (app.activeDocument.activeLayers || []).length;
    note('Exporting ' + (n === 1 ? 'the selected layer' : n + ' selected layers') + '…');
    const res = await runExclusive(() => TL.exportSelectedLayersJpg(app.activeDocument, constants, folder, (nm) => base + pexSuffix(nm) + '.jpg'));
    if (!res.ok) {
      flash(posterExpBtn, '✗', 'Poster');
      note(res.reason === 'no-layer' ? 'Select the poster layer(s) first, then press Poster.' : 'Poster export failed.');
      return;
    }
    if (ctx) {
      for (const f of res.files) {
        const bytes = await R.readBytes(f.entry);
        try {
          await R.putBytes('/api/maker-tools/poster-exports/' + encodeURIComponent(f.filename), bytes);
          note('Saved "' + f.filename + '" → Posterflow poster folder.');
        } catch (e) {
          if (!/HTTP 400/.test(String(e && e.message))) throw e;
          note('Server has no poster export folder; saving locally instead.');
          const localFolder = await FS.getPosterFolder({});
          if (!localFolder) break;
          await FS.writeFileBytes(localFolder, f.filename, bytes);
          note('Saved "' + f.filename + '" → ' + localFolder.name);
        }
      }
    } else {
      res.files.forEach((f) => note('Saved "' + f.filename + '" → ' + res.folderName));
    }
    flash(posterExpBtn, '✓', 'Poster');
  } catch (e) { flash(posterExpBtn, '✗', 'Poster'); showError(e); }
}

// ---- Square Art crop ----
// Temp-tab flow (mirrors the Photopea panels): duplicate + isolate the POSTER art into its own
// tab, presets/marquee pick the square there, Crop snaps non-square selections visibly, then
// crops the DUPLICATE natively, saves, closes the tab, and returns to the PSD.
const SQ_MIN = 500;   // square art must be at least 500×500

// Edge keeper: a square dragged past the canvas edge keeps its size and sits partly outside
// (geometry.keepSquareInside), so slide it back once the drag ends. UXP offers no hook during the
// drag — a selection move fires toolModalStateChanged / historyStateChanged only after the mouse is
// released, and sometimes nothing at all — so those notifications are backed by a light poll while
// the crop tab is armed. Read + re-select run as one exclusive op so a preset click can't interleave.
let sqKeepBusy = false, sqKeepT = null, sqKeepPoll = null;
async function sqKeepInside() {
  if (!sqArmed || !sqTemp || sqBusy || sqKeepBusy) return;
  sqKeepBusy = true;
  try {
    if (!hasDoc() || app.activeDocument.id !== sqTemp.doc.id) return;
    const fix = await runExclusive(async () => {
      const s = await TL.readSelectionBounds(sqTemp.doc);
      const f = s && G.keepSquareInside(s);
      if (f) await TL.setSquareSelection(sqTemp.doc, f.x, f.y, f.side);
      return f;
    });
    if (fix) note('Moved the ' + fix.side + '×' + fix.side + ' selection back onto the canvas.');
  } catch (_) {
  } finally { sqKeepBusy = false; }
}
const sqKeepSoon = () => {
  if (!sqArmed) return;
  if (sqKeepT) clearTimeout(sqKeepT);
  sqKeepT = setTimeout(() => { sqKeepT = null; sqKeepInside(); }, 120);
};
const SQ_EVENTS = ['toolModalStateChanged', 'historyStateChanged', 'set'];   // listened to only while armed

function sqSetUI(armed) {
  sqArmed = armed;
  if (armed && !sqKeepPoll) {
    sqKeepPoll = setInterval(sqKeepInside, 600);
    try { action.addNotificationListener(SQ_EVENTS, sqKeepSoon); } catch (_) {}
  }
  if (!armed && sqKeepPoll) {
    clearInterval(sqKeepPoll); sqKeepPoll = null;
    try { action.removeNotificationListener(SQ_EVENTS, sqKeepSoon); } catch (_) {}
  }
  sqPresetsEl.classList.toggle('hidden', !armed);
  sqCancelBtn.classList.toggle('hidden', !armed);
  posterExpBtn.classList.toggle('hidden', armed);   // Poster steps aside while Square Art works
  document.querySelector('.sqrow').classList.toggle('armed', armed);
  squareArtBtn.textContent = armed ? 'Crop' : 'Square Art';
}
async function sqCleanup() {   // close the crop tab, return home, hand the Move tool back
  const t = sqTemp; sqTemp = null;
  if (t) await runExclusive(() => TL.closeCropDoc(t.doc, sqHome));
  sqHome = null; sqCtx = null; sqWant = 0;
  TL.selectTool('moveTool');
}
const flashSq = (m) => {
  sqBusy = false; sqSetUI(false);
  squareArtBtn.textContent = m; setTimeout(() => { if (!sqArmed) squareArtBtn.textContent = 'Square Art'; }, 1400);
};
async function onSquareArt(ev) {
  if (sqBusy) return;
  const repick = wantsRepick(ev);
  if (!sqArmed) {
    if (!hasDoc()) { note('No document open.'); return; }
    sqBusy = true; squareArtBtn.textContent = '…';
    posterExpBtn.classList.add('hidden');
    try {
      sqHome = app.activeDocument;
      sqCtx = remoteCtx();
      const res = await runExclusive(() => TL.makeCropDoc(sqHome, constants));
      if (!res.ok) {
        flashSq('✗');
        note(res.reason === 'noposter' ? 'No POSTER group in this document.'
          : res.reason === 'empty' ? 'The POSTER group has no visible variant.' : 'Square Art failed.');
        await sqCleanup();
        return;
      }
      sqTemp = res;
      sqBusy = false; sqSetUI(true);
      TL.selectTool('marqueeRectTool');
      note('Opened the poster art (' + res.width + '\u00d7' + res.height + ') in its own tab. Pick a preset or drag a marquee square, then press Crop.');
      await onSqPreset(1000);   // start with a 1000×1000 selection placed — saves a click
    } catch (e) { flashSq('✗'); showError(e); await sqCleanup(); }
    return;
  }
  // Armed → the selection lives ON the crop doc, so it reads correctly even if the user switched tabs.
  sqBusy = true; squareArtBtn.textContent = '…';
  try {
    const s = await TL.readSelectionBounds(sqTemp.doc);
    if (!s) { sqBusy = false; squareArtBtn.textContent = 'Crop'; note('No selection. Pick a preset or drag a square with the marquee tool (M).'); return; }
    let side = Math.round(Math.min(s.r - s.l, s.b - s.t, s.cw, s.ch));
    if (side < SQ_MIN) { sqBusy = false; squareArtBtn.textContent = 'Crop'; note('Selection is ' + side + 'px; square art must be at least ' + SQ_MIN + '×' + SQ_MIN + '.'); return; }
    const x = Math.max(0, Math.min(Math.round((s.l + s.r) / 2 - side / 2), s.cw - side));
    const y = Math.max(0, Math.min(Math.round((s.t + s.b) / 2 - side / 2), s.ch - side));
    // Non-square → snap the on-canvas selection so the user sees the exact crop first.
    if (Math.abs(Math.round(s.r - s.l) - Math.round(s.b - s.t)) > 2) {
      await runExclusive(() => TL.setSquareSelection(sqTemp.doc, x, y, side));
      sqBusy = false; squareArtBtn.textContent = 'Crop';
      note('Snapped the selection to ' + side + '×' + side + '. Adjust it if needed, then press Crop.');
      return;
    }
    // Upscale to the wanted preset size ONLY when the art itself capped the selection.
    const want = (sqWant > side && side >= Math.min(sqTemp.width, sqTemp.height)) ? sqWant : 0;
    const ctx = sqCtx;
    const folder = ctx ? await R.tempFolder() : await FS.getSquareartFolder({ forcePick: repick });
    if (!folder) { sqBusy = false; squareArtBtn.textContent = 'Crop'; note('Square Art cancelled: no folder chosen.'); return; }
    const filename = ((ctx && ctx.name) || baseName(sqHome)) + ' - squareart.jpg';
    const res = await runExclusive(() => TL.cropSaveSquare(sqTemp.doc, folder, filename, { x, y, side }, want));
    if (!res.ok) { flashSq('✗'); note('Square Art export failed.'); await sqCleanup(); return; }
    const sizeTxt = res.side + '×' + res.side + (res.srcSide < res.side ? ' (art was ' + res.srcSide + 'px, upscaled)' : '');
    if (ctx) {
      const bytes = await R.readBytes(res.entry);
      try {
        await R.putBytes('/api/maker-tools/squareart-exports/' + encodeURIComponent(res.filename), bytes);
        note('Saved "' + res.filename + '" (' + sizeTxt + ') → Posterflow square art folder.');
      } catch (e) {
        if (!/HTTP 400/.test(String(e && e.message))) throw e;
        note('Server has no square art folder; saving locally instead.');
        const localFolder = await FS.getSquareartFolder({});
        if (localFolder) { await FS.writeFileBytes(localFolder, res.filename, bytes); note('Saved "' + res.filename + '" (' + sizeTxt + ') → ' + localFolder.name); }
      }
    } else {
      note('Saved "' + res.filename + '" (' + sizeTxt + ') → ' + res.folderName);
    }
    await sqCleanup();
    flashSq('✓');
  } catch (e) { flashSq('✗'); showError(e); await sqCleanup(); }
}
async function onSqPreset(size) {
  if (sqBusy || !sqArmed || !sqTemp) return;
  sqWant = size;   // remembered so a preset larger than the art can upscale the output to match
  const s = Math.min(size, sqTemp.width, sqTemp.height);
  const x = Math.round((sqTemp.width - s) / 2), y = Math.max(0, Math.min(100, sqTemp.height - s));   // centered, 100px from the top
  try {
    await runExclusive(() => TL.setSquareSelection(sqTemp.doc, x, y, s));
    // batchPlay can no-op without rejecting — only claim success if the selection really exists.
    const check = await TL.readSelectionBounds(sqTemp.doc);
    if (check) note('Placed a ' + s + '×' + s + ' selection. Drag inside it to position, then press Crop.');
    else note('Could not place the selection. Drag one with the marquee tool instead.');
  } catch (e) { showError(e); }
}
async function onSqCancel() {
  if (sqBusy) return;
  sqSetUI(false);
  await sqCleanup();
}

// ---- Finish (right-click MOVIE / SHOW): tidy the layers, save, export, hand off to Square Art ----
// MOVIE:            clear → POSTER layer → "main" → LOGO layer → "Title (Year) - Logo" → [trim] → SAVE
//                   → JPG → logo PNG → Square Art
// SHOW (orange ⚡):  clear → logo name → [trim] → SAVE → tag check → tag batch, unattended
//                   → logo PNG → show poster only → SAVE again → Square Art
// SHOW (blue ⚡):    same up to SAVE, then the range box waits for Run; the tail resumes from the logo PNG.
// Prep failures are recorded but never skip the save; after a failed prep step nothing exports (a
// stray visible season layer would misname the JPG). One checklist note reports every run.
let finishBusy = false;   // a pipeline is running, or the blue path is waiting on the range box
let finishWait = null;    // blue: { docId, resume(result) } until Run / ✕ / a bolt click
// ⚙ finish steps: every step of a right-click finish can be unticked; the order never changes.
// Defaults: everything on except trim (it deletes off-canvas pixels and the PSD saves right after).
const STEP_DEFAULTS = {
  movie: { clear: true, main: true, logoName: true, trim: false, save: true, jpg: true, logoPng: true, squareArt: true },
  show:  { clear: true, logoName: true, trim: false, save: true, tagCheck: true, batch: true, logoPng: true, showOnly: true, saveAgain: true, squareArt: true },
};
const STEP_LABELS = {   // [key, chip text, what it does] in pipeline order
  movie: [
    ['clear', 'clear', 'Clear all season / SP / C / CLS layers first: a stray visible season layer would misname the JPG'],
    ['main', 'main', 'Rename the visible POSTER layer to "main"'],
    ['logoName', 'logo name', 'Rename the visible LOGO layer to "Title (Year) - Logo"'],
    ['trim', 'trim', 'Trim off-canvas pixels: they are deleted, and the PSD saves right after'],
    ['save', 'save', 'Save the PSD: a failed save stops the finish'],
    ['jpg', 'JPG', 'Export the flattened JPG'],
    ['logoPng', 'logo PNG', 'Export the LOGO group as a transparent PNG'],
    ['squareArt', 'Square Art', 'Open Square Art with the default square placed'],
  ],
  show: [
    ['clear', 'clear', 'Clear all season / SP / C / CLS layers first'],
    ['logoName', 'logo name', 'Rename the visible LOGO layer to "Title (Year) - Logo"'],
    ['trim', 'trim', 'Trim off-canvas pixels: they are deleted, and the PSD saves right after'],
    ['save', 'save', 'Save the PSD before the batch: a failed save stops the finish'],
    ['tagCheck', 'tag check', 'Check the tags before the batch: any problem stops the finish (orange only)'],
    ['batch', 'batch', 'Run the batch (orange: every tag, unattended; blue: the seasons you type)'],
    ['logoPng', 'logo PNG', 'Export the LOGO group as a transparent PNG'],
    ['showOnly', 'show only', 'Hide the season text and show only the show-tagged poster'],
    ['saveAgain', 'save again', 'Save the PSD again so it closes without a prompt'],
    ['squareArt', 'Square Art', 'Open Square Art with the default square placed'],
  ],
};
function loadSteps() {
  const out = { movie: Object.assign({}, STEP_DEFAULTS.movie), show: Object.assign({}, STEP_DEFAULTS.show) };
  try {
    const saved = JSON.parse(localStorage.getItem('posterflow.finishSteps') || '{}');
    ['movie', 'show'].forEach((f) => Object.keys(out[f]).forEach((k) => { if (saved[f] && typeof saved[f][k] === 'boolean') out[f][k] = saved[f][k]; }));
  } catch (_) {}
  return out;
}
const steps = loadSteps();
const optBar = document.querySelector('.optbar');
function renderOptions() {   // one toggle chip per step, green = runs, grey = skipped
  ['movie', 'show'].forEach((f) => {
    const row = document.querySelector('.optrow[data-steps="' + f + '"]');
    row.innerHTML = '';
    STEP_LABELS[f].forEach(([k, text, why]) => {
      const c = chip(text, steps[f][k], false);
      tip(c, why);
      c.addEventListener('click', () => {
        steps[f][k] = !steps[f][k];
        try { localStorage.setItem('posterflow.finishSteps', JSON.stringify(steps)); } catch (_) {}
        renderOptions(); refresh();   // re-tint here; the MOVIE / SHOW chip tips list whichever steps are on
      });
      row.appendChild(c);
    });
  });
}
renderOptions();
document.querySelector('[data-act="options"]').addEventListener('click', () => optBar.classList.toggle('hidden'));
document.querySelector('[data-act="options-close"]').addEventListener('click', () => optBar.classList.add('hidden'));
const stepList = (f) => {
  const names = STEP_LABELS[f].filter(([k]) => steps[f][k]).map(([, l]) => l);
  return names.length ? names.join(' \u2192 ') : 'nothing (every step is off in \u2699)';
};
const finishTip = (tag) => tag === 'main'
  ? 'Right-click: finish the movie poster: ' + stepList('movie')
  : 'Right-click: finish the show with the orange \u26a1 (unattended)  \u00b7  Shift+right-click: with the blue \u26a1 (you type the seasons).  Steps: ' + stepList('show');
// "X and Y skipped" for whichever of the remaining steps are on, else "stopped".
const skippedTail = (f, keys) => {
  const names = STEP_LABELS[f].filter(([k]) => keys.indexOf(k) >= 0 && steps[f][k]).map(([, l]) => l);
  return names.length ? names.join(' and ') + ' skipped' : 'stopped';
};

function rootPosterGroup() {
  const top = app.activeDocument.layers;
  for (let i = 0; i < top.length; i++) {
    if (top[i].kind === constants.LayerKind.GROUP && /^\s*poster\s*$/i.test(top[i].name)) return top[i];
  }
  return null;
}
const scanTexts = (scan) => ({ season: scan.seasonText, specials: scan.specialsText, cls: scan.clsText, collection: scan.collectionText });
const jpgs = (n) => n + ' JPG' + (n === 1 ? '' : 's');

// Checklist runner: ✓ step · ✓ step (info) · – step skipped (why) · ✗ step: why
function finishRun(label) {
  const parts = [], failed = [];
  const step = async (name, fn) => {
    try {
      const r = await fn();
      parts.push(r && r.skipped ? '\u2013 ' + name + ' skipped (' + r.skipped + ')' : '\u2713 ' + name + (r && r.info ? ' (' + r.info + ')' : ''));
      return true;
    } catch (e) { parts.push('\u2717 ' + name + ': ' + (e && e.message ? e.message : e)); failed.push(name); return false; }
  };
  const report = (tail) => note(label + ': ' + parts.concat(tail ? [tail] : []).join('  \u00b7  '));
  return { step, report, failed };
}

const stepClear = () => applyChangesCore(T.clearAll(model));
const stepMain = () => renameVisibleIn(rootPosterGroup(), 'POSTER', 'main', true);
const stepLogoName = async () => {
  const grp = findLogoGroupLive(app.activeDocument.layers);
  if (!grp) return { skipped: 'no LOGO group' };
  await renameVisibleIn(grp, 'LOGO', logoLayerName(), false);
};
const stepTrim = () => runExclusive(() => TL.trim(app.activeDocument));
const stepJpg = async () => { const r = await jpgCore(false); if (!r.ok) throw new Error(r.msg); };
const stepLogoPng = async () => {
  if (!findLogoGroupLive(app.activeDocument.layers)) return { skipped: 'no LOGO group' };
  const r = await logoCore(); if (!r.ok) throw new Error(r.msg);
};
const stepTagBatch = async () => {
  await startBatch('tags');
  if (!batchConvention || !batchItems.length) throw new Error('nothing to export');
  const r = await runBatch();
  if (!r.ok) throw new Error(r.reason);
  return { info: jpgs(r.count) };
};
// Hide season / SP / C / CLS text and make the show-tagged poster the only visible POSTER layer.
const stepShowOnly = async () => {
  await refresh();
  await applyChangesCore(T.clearAll(model));
  const base = baseName();
  const scan = M.scanBatch(lastTree || app.activeDocument, constants, B.normName(base));
  const item = B.buildTagItems(scan.variants, scanTexts(scan), base).items.find((it) => it.key === 'show');
  if (!item) return { skipped: 'no show-tagged layer' };
  await runExclusive(() => writeVisible(pairsFor(item.changes), 'Show poster only'));
  await refresh();
};

async function finishGuard(label) {
  if (finishBusy || batchBusy || sqBusy || sqArmed || busy) { note(label + ': busy (close Square Art or wait for the current step).'); return false; }
  if (!hasDoc()) { note(label + ': no document open.'); return false; }
  await refresh();
  if (!rootPosterGroup()) { note(label + ': not a poster PSD (no POSTER group at the top level). Nothing was changed.'); return false; }
  return true;
}

async function onFinishMovie() {
  const label = 'MOVIE', S_ = steps.movie;
  if (!(await finishGuard(label))) return;
  finishBusy = true;
  const F = finishRun(label);
  try {
    if (S_.clear) await F.step('cleared', stepClear);
    if (S_.main) await F.step('main', stepMain);
    if (S_.logoName) await F.step('logo name', stepLogoName);
    if (S_.trim) await F.step('trim', stepTrim);
    if (S_.save) {
      if (!(await F.step('saved', saveCore))) { flash(saveBtn, '✗', '💾'); F.report('STOPPED, PSD not saved'); return; }
      flash(saveBtn, '✓', '💾');
    }
    if (F.failed.length) { F.report((S_.save ? 'PSD saved; ' : '') + skippedTail('movie', ['jpg', 'logoPng', 'squareArt'])); return; }
    if (S_.jpg && !(await F.step('JPG', stepJpg))) { F.report(skippedTail('movie', ['logoPng', 'squareArt'])); return; }
    if (S_.logoPng && !(await F.step('logo PNG', stepLogoPng))) { F.report(skippedTail('movie', ['squareArt'])); return; }
    if (S_.squareArt) { F.report('opening Square Art'); await onSquareArt(); }
    else F.report('done');
  } catch (e) { showError(e); }
  finally { finishBusy = false; }
}

async function finishShowTail(F) {
  const S_ = steps.show;
  if (S_.logoPng && !(await F.step('logo PNG', stepLogoPng))) { F.report(skippedTail('show', ['showOnly', 'saveAgain', 'squareArt'])); return; }
  const shown = S_.showOnly ? await F.step('show only', stepShowOnly) : true;
  if (S_.saveAgain) {   // the first save holds the work; this one spares a prompt on close
    const saved = await F.step('saved again', saveCore);
    flash(saveBtn, saved ? '✓' : '✗', '💾');
  }
  if (!shown) { F.report(skippedTail('show', ['squareArt'])); return; }
  if (S_.squareArt) { F.report('opening Square Art'); await onSquareArt(); }
  else F.report('done');
}

async function onFinishShow(mode) {   // 'tags' = orange ⚡ (unattended), 'seasons' = blue ⚡ (waits for Run)
  const label = 'SHOW (' + (mode === 'seasons' ? 'blue' : 'orange') + ' \u26a1)', S_ = steps.show;
  if (!(await finishGuard(label))) return;
  finishBusy = true;
  const F = finishRun(label);
  let waiting = false;
  const TAIL = ['logoPng', 'showOnly', 'saveAgain', 'squareArt'];
  try {
    if (S_.clear) await F.step('cleared', stepClear);
    if (S_.logoName) await F.step('logo name', stepLogoName);
    if (S_.trim) await F.step('trim', stepTrim);
    if (S_.save) {
      if (!(await F.step('saved', saveCore))) { flash(saveBtn, '✗', '💾'); F.report('STOPPED, PSD not saved'); return; }
      flash(saveBtn, '✓', '💾');
    }
    if (F.failed.length) { F.report((S_.save ? 'PSD saved; ' : '') + skippedTail('show', ['batch'].concat(TAIL))); return; }
    if (!S_.batch) { await finishShowTail(F); return; }
    if (mode === 'tags') {
      if (S_.tagCheck) {
        const base = baseName();
        await refresh();   // the prep steps renamed layers; check the tags as they are now
        const probs = B.tagProblems(M.scanBatch(lastTree || app.activeDocument, constants, B.normName(base)), base);
        if (probs.length) { F.report('STOPPED. Fix these tags, then right-click SHOW again:  ' + probs.join('   \u00b7   ')); return; }
      }
      if (!(await F.step('tag batch', stepTagBatch))) { F.report(skippedTail('show', TAIL)); return; }
      await finishShowTail(F);
    } else {
      await startBatch('seasons');
      if (batchRow.classList.contains('hidden')) { F.report('no season layers to batch; ' + skippedTail('show', TAIL)); return; }
      finishWait = {
        docId: app.activeDocument.id,
        resume: async (r) => {
          try {
            const ran = await F.step('season batch', async () => { if (!r.ok) throw new Error(r.reason); return { info: jpgs(r.count) }; });
            if (ran) await finishShowTail(F); else F.report(skippedTail('show', TAIL));
          } catch (e) { showError(e); }
          finally { finishBusy = false; }
        },
      };
      waiting = true;
      F.report((S_.save ? 'PSD saved. ' : '') + 'Type the seasons and press Run');
    }
  } catch (e) { showError(e); }
  finally { if (!waiting) finishBusy = false; }
}

function abandonFinish(why) {
  if (!finishWait) return;
  finishWait = null; finishBusy = false;
  note('SHOW (blue \u26a1): ' + why + '; ' + skippedTail('show', ['logoPng', 'showOnly', 'saveAgain', 'squareArt']) + '.');
}
// Run / Enter in the range box: run the batch, then resume a waiting blue finish on the same document.
async function runBatchFromUI() {
  const r = await runBatch();
  if (!finishWait || r.reason === 'nothing' || r.reason === 'busy') return;   // bar still open — fix the input, press Run again
  const w = finishWait; finishWait = null;
  if (!hasDoc() || app.activeDocument.id !== w.docId) { finishBusy = false; note('SHOW (blue \u26a1): the document changed while waiting; finish abandoned after the batch.'); return; }
  await w.resume(r);
}

// ---- Batch export ----
const batchNote = (t) => { batchMsgEl.textContent = t; };
const modeBtn = () => batchMode === 'seasons' ? seasonsBtn : batchBtn;
const resetBolts = () => { batchBtn.textContent = '⚡'; seasonsBtn.textContent = '⚡'; };
const closeBatch = () => { if (!batchBusy) batchBar.classList.add('hidden'); };

async function startBatch(mode) {
  if (batchBusy) return;
  batchMode = mode;
  batchBar.classList.remove('hidden'); batchRow.classList.add('hidden'); batchHelp.classList.add('hidden');
  batchNote('Scanning…');
  if (!hasDoc()) { batchNote('No document open.'); return; }
  await refresh();   // rebuild model + layerByPath so batch changes apply to current layers
  if (batchMode === 'tags') {
    const base = baseName();
    const scan = M.scanBatch(lastTree || app.activeDocument, constants, B.normName(base));
    const built = B.buildTagItems(scan.variants, scanTexts(scan), base);
    if (!built.items.length) {
      built.warnings.forEach((w) => note('Batch: ' + w));   // nothing to run — the reasons ARE the answer
      batchNote('No name-tag layers (s0/s1…, s1-8, main/show/movie/poster, c, cls) in this document. For a single poster with a SEASONS group, use the blue ⚡ instead.');
      return;
    }
    batchConvention = true;
    batchItems = built.items;
    batchWarnings = built.warnings;   // shown when the run actually starts, not at this preview
    batchRow.classList.remove('hidden'); batchInput.classList.remove('hidden');
    batchInput.value = ''; batchInput.setAttribute('placeholder', 'blank = all · filter: c, cls, main, s1-8');
    batchNote('Found ' + batchItems.length + ' tagged export(s)' + (built.warnings.length ? ' (' + built.warnings.length + ' warning(s))' : '') + '. Run all, or type a filter.');
  } else {
    batchWarnings = [];
    if (!(T.byRole(model, 'season').length || model.singles.length)) { batchNote('No SEASONS group or season layers found. For layers tagged s0/s1…/main/show, use the amber ⚡ instead.'); return; }
    batchConvention = false;
    batchRow.classList.remove('hidden'); batchInput.classList.remove('hidden');
    batchInput.value = ''; batchInput.setAttribute('placeholder', 'e.g. 8, 0-5, or blank = all'); batchInput.focus();
    batchNote('Which seasons? e.g. "8" (=1–8), "0-5" (0 = Specials), "2015-2020" (years), or blank = all.');
  }
}

async function runBatch() {   // → { ok, count } or { ok: false, reason: 'busy' | 'nothing' | 'no folder chosen' | <error> }
  if (batchBusy) return { ok: false, reason: 'busy' };
  if (!batchConvention) {
    const r = B.parseRange(batchInput.value);
    batchItems = (B.isYearRange(r) && T.byRole(model, 'year').length) ? B.yearItems(model, r.start, r.end) : B.seasonItems(model, r.start, r.end);
  } else {
    const fk = B.parseTagFilter(batchInput.value);
    if (fk) batchItems = batchItems.filter((it) => fk[it.key]);
  }
  if (!batchItems.length) { batchNote('Nothing matched, nothing to export.'); return { ok: false, reason: 'nothing' }; }
  batchBusy = true; modeBtn().textContent = '…'; batchRow.classList.add('hidden');
  batchWarnings.forEach((w) => note('Batch: ' + w));
  const style = curStyle, batchCtx = remoteCtx(), base = (batchCtx && batchCtx.name) || baseName();
  let ok = 0, result = { ok: false, reason: 'error', count: 0 };
  try {
    // Gradient safety net: turn a forgotten-off GRADIENT group on (and leave it on) before exporting.
    if (gradChk.checked) {
      try {
        if (await runExclusive(() => TL.forceGradientVisible(app.activeDocument, constants)))
          note('The GRADIENT group had hidden layers; turned on and left visible in the PSD.');
      } catch (_) {}
    }
    // Manual collection logos (c/c1/c2… in LOGO) stay hidden for non-collection exports.
    try { await runExclusive(() => TL.hideManualCollectionLogos(app.activeDocument, constants)); } catch (_) {}
    for (let i = 0; i < batchItems.length; i++) {
      batchNote('Exporting ' + (i + 1) + '/' + batchItems.length + '…');
      const changes = batchItems[i].changes;
      await runExclusive(() => writeVisible(pairsFor(changes), 'Batch variant ' + (i + 1)));
      // Collection export: temporary logo copy on the collection ruler (removed right after).
      // Placement/cleanup are best-effort — any failure warns and the export proceeds bare.
      let placed = null;
      if (batchItems[i].collection) {
        try { placed = await runExclusive(() => TL.placeCollectionLogo(app.activeDocument, constants)); }
        catch (e) { placed = null; note('Batch: collection logo placement failed (' + (e && e.message ? e.message : e) + '); exported without repositioning the logo.'); }
        if (placed && placed.warning) note('Batch: ' + placed.warning);
        if (placed && placed.note) note('Batch: ' + placed.note);
      }
      let res;
      try {
        if (batchCtx) {
          try {
            res = await runExclusive(() => S.exportJpgRemote(app.activeDocument, batchCtx, base, batchItems[i].suffix));
          } catch (e) {
            if (!/HTTP 400/.test(String(e && e.message))) throw e;
            if (i === 0) note('Server has no image export folder; saving batch locally instead.');
            res = await runExclusive(() => S.exportJpg(app.activeDocument, batchCtx.style, base, batchItems[i].suffix));
          }
        } else {
          res = await runExclusive(() => S.exportJpg(app.activeDocument, style, base, batchItems[i].suffix));
        }
      } finally {
        if (placed && placed.placed) {
          try { await runExclusive(() => TL.removeCollectionLogo(app.activeDocument, constants, placed)); } catch (_) {}
        }
      }
      if (res.ok) { ok++; }
      else if (res.reason === 'cancelled') { batchNote('Stopped: no folder chosen.'); result.reason = 'no folder chosen'; return result; }
    }
    batchNote('Done: exported ' + ok + ' JPG' + (ok === 1 ? '' : 's') + '.');
    result = { ok: true, count: ok };
  } catch (e) {
    batchNote('Stopped: ' + (e && e.message ? e.message : e));
    result.reason = String(e && e.message ? e.message : e);
  } finally {
    result.count = ok;
    batchBusy = false; resetBolts(); refresh(); setTimeout(closeBatch, 2600);
  }
  return result;
}

// Each bolt toggles ITS mode: click the same bolt again to dismiss; click the other to switch modes.
function boltClick(mode) {
  if (batchBusy) return;
  abandonFinish('season batch cancelled');   // a manual bolt click overrides a finish waiting on the range box
  if (!batchBar.classList.contains('hidden') && batchMode === mode) { closeBatch(); return; }
  startBatch(mode);
}

// ---- wire controls ----
saveBtn.addEventListener('click', onSave);
jpgBtn.addEventListener('click', onJpg);
logoBtn.addEventListener('click', () => onPlace('logo', logoBtn, 'Place Logo'));
fitBtn.addEventListener('click', () => onPlace('fit', fitBtn, 'Fit Poster'));
trimBtn.addEventListener('click', onTrim);
logoExpBtn.addEventListener('click', onLogoExport);
posterExpBtn.addEventListener('click', onPosterExport);
squareArtBtn.addEventListener('click', onSquareArt);
sqCancelBtn.addEventListener('click', onSqCancel);
document.querySelectorAll('[data-sq]').forEach((b) => b.addEventListener('click', () => onSqPreset(parseInt(b.getAttribute('data-sq'), 10))));
document.querySelector('[data-act="folders"]').addEventListener('click', () => {
  FS.clearAllFolders();
  note('Remembered folders cleared; the next JPG / Logo / Square Art export will ask again.');
});
document.querySelector('[data-act="refresh"]').addEventListener('click', refresh);
batchBtn.addEventListener('click', () => boltClick('tags'));
seasonsBtn.addEventListener('click', () => boltClick('seasons'));
document.querySelector('[data-act="batch-run"]').addEventListener('click', runBatchFromUI);
document.querySelector('[data-act="batch-help"]').addEventListener('click', () => {
  const rows = batchMode === 'tags'
    ? [['blank', 'run everything'], ['c', 'Collection'], ['cls', 'Limited Series'], ['main / show', 'main poster'],
       ['s3 or 3', 'one season'], ['0', 'Specials'], ['s1-8', 'a season range'], ['main, c', 'combine with commas']]
    : [['blank', 'all seasons + Specials'], ['8', 'seasons 1-8'], ['3-6', 'that range'], ['0-5', 'includes Specials'], ['2015-2020', 'year layers']];
  batchHelp.innerHTML = rows.map((r) => '<div><b>' + r[0] + '</b>: ' + r[1] + '</div>').join('');
  batchHelp.classList.toggle('hidden');
});
document.querySelector('[data-act="batch-cancel"]').addEventListener('click', () => { abandonFinish('season batch cancelled'); closeBatch(); });
batchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runBatchFromUI(); } });

// ---- Posterflow server link: settings bar + queue poller --------------------
const linkBar   = document.querySelector('.linkbar');
const linkMsg   = document.querySelector('.linkmsg');
const linkUrl   = document.querySelector('.linkurl');
const linkPass  = document.querySelector('.linkpass');
const linkToggleBtn = document.querySelector('[data-act="link-toggle"]');

let linkStatus = null;   // last poll result: null = none yet, { ok: true } | { ok: false, msg }

function updateLinkMsg() {
  const cfg = R.getConfig();
  if (!(cfg.enabled && cfg.url)) {
    linkMsg.textContent = 'Posterflow server link is off. Enter the server URL (plain http works on Windows; Mac needs https) and the app password if one is set, then Save and Enable.';
  } else if (!linkStatus) {
    linkMsg.textContent = 'Connecting to ' + R.baseUrl(cfg) + '…';
  } else if (linkStatus.ok) {
    linkMsg.textContent = 'Connected to ' + R.baseUrl(cfg) + '. "PS" exports open here automatically.';
  } else {
    linkMsg.textContent = 'Can\'t reach ' + R.baseUrl(cfg) + ': ' + linkStatus.msg + '. (Plain http works on Windows only; Mac needs https.)';
  }
}

function refreshLinkBar() {
  const cfg = R.getConfig();
  linkUrl.value = cfg.url || '';
  linkPass.value = cfg.password || '';
  linkToggleBtn.textContent = cfg.enabled ? 'Disable' : 'Enable';
  updateLinkMsg();
}
document.querySelector('[data-act="link"]').addEventListener('click', () => {
  if (linkBar.classList.contains('hidden')) { refreshLinkBar(); linkBar.classList.remove('hidden'); }
  else linkBar.classList.add('hidden');
});
document.querySelector('[data-act="link-close"]').addEventListener('click', () => linkBar.classList.add('hidden'));
document.querySelector('[data-act="link-save"]').addEventListener('click', () => {
  const cfg = R.getConfig();
  cfg.url = String(linkUrl.value || '').trim();
  cfg.password = String(linkPass.value || '');
  R.setConfig(cfg);
  linkStatus = null;
  refreshLinkBar();
  note('Posterflow server link saved.');
});
linkToggleBtn.addEventListener('click', () => {
  const cfg = R.getConfig();
  cfg.url = String(linkUrl.value || '').trim() || cfg.url;
  cfg.enabled = !cfg.enabled;
  R.setConfig(cfg);
  linkStatus = null;
  refreshLinkBar();
  note(cfg.enabled ? 'Posterflow link enabled, watching the queue.' : 'Posterflow link disabled.');
});

// Poll the server queue and open claimed PSDs. Skipped while any operation is running; errors are
// throttled to one note per streak so a down server doesn't spam.
let pollErrNoted = false;
setInterval(async () => {
  if (busy || batchBusy || sqBusy) return;
  if (!R.isConfigured(R.getConfig())) return;
  let items;
  try { items = await R.pollQueue(); }
  catch (e) {
    const msg = e && e.message ? e.message : String(e);
    linkStatus = { ok: false, msg };
    updateLinkMsg();
    if (!pollErrNoted) { pollErrNoted = true; note('Posterflow link: ' + msg); }
    return;
  }
  pollErrNoted = false;
  linkStatus = { ok: true };
  updateLinkMsg();
  for (const item of items) {
    try {
      note('Opening "' + item.filename + '" from Posterflow…');
      const opened = await runExclusive(() => R.openQueued(item));
      remoteDocs[opened.doc.id] = { filename: item.filename, style: item.style, name: item.name, entry: opened.entry };
      refresh();
    } catch (e) {
      note('Could not open "' + item.filename + '": ' + (e && e.message ? e.message : e));
    }
  }
}, 3000);

// Auto-rescan: Photoshop notifies on layer show/hide and doc open/close — re-read the panel when
// they fire (debounced, skipped while our own ops or a batch run are mid-flight; a refresh right
// after our own toggles is harmless and re-syncs rv). A light 2s poll catches active-doc SWITCHES,
// which don't fire those events.
let rescanT = null;
const scheduleRescan = () => {
  if (rescanT) clearTimeout(rescanT);
  rescanT = setTimeout(() => { rescanT = null; if (!busy && !batchBusy && !sqBusy && Date.now() >= selfEditUntil) refresh(); }, 500);
};
try {
  action.addNotificationListener(['show', 'hide', 'open', 'close'], scheduleRescan);
} catch (e) { console.log('Posterflow: event listener unavailable, relying on poll only.', e); }
setInterval(() => {
  if (busy || batchBusy || sqBusy) return;
  const id = hasDoc() ? app.activeDocument.id : 0;
  if (id !== lastDocId) refresh();
}, 2000);

refresh();   // read whatever's open when the panel mounts
