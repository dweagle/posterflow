// Panel controller. Replaces the Photopea panel's postMessage/echo plumbing with direct photoshop
// DOM calls. Every document mutation (visibility toggles, save, JPG, place, trim, batch) runs through
// one serialized modal queue (runExclusive → core.executeAsModal) because UXP forbids overlapping
// modal scopes.
'use strict';

const { app, core, constants } = require('photoshop');
const T = require('./toggle');
const M = require('./model');
const S = require('./save');
const B = require('./batch');
const TL = require('./tools');
const FS = require('./fs');
const R = require('./remote');
const SC = require('./squarecrop');

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
const textlessExpBtn = document.querySelector('[data-act="textless-export"]');
const squareArtBtn = document.querySelector('[data-act="squareart"]');
const batchBtn    = document.querySelector('[data-act="batch"]');          // amber ⚡ — name-tag batch
const seasonsBtn  = document.querySelector('[data-act="batch-seasons"]');  // blue ⚡ — seasons from one poster
const batchBar    = document.querySelector('.batchbar');
const batchMsgEl  = document.querySelector('.batchmsg');
const batchRow    = document.querySelector('.batchrow');
const batchInput  = document.querySelector('.batchinput');
const batchHelp   = document.querySelector('.batchhelp');

// ---- state ----
let model = { singles: [], seasons: [], sequels: [], seqGroup: null };
let layerByPath = {};
let curStyle = '';
let curLogoGroup = null;   // path of the LOGO group in the active doc (null = none → Logo button hidden)
const remoteDocs = {};     // doc.id → { filename, style, name, entry } for docs opened from the Posterflow queue
const remoteCtx = () => (hasDoc() ? remoteDocs[app.activeDocument.id] : null) || null;
let batchMode = 'tags', batchConvention = false, batchItems = [], batchWarnings = [], batchBusy = false;

// UXP widget click events don't reliably carry modifier flags (altKey is false on Windows), so track
// the Alt key ourselves as a fallback signal for the "re-pick folder" gestures.
let altDown = false;
window.addEventListener('keydown', (e) => { if (e.key === 'Alt') altDown = true; });
window.addEventListener('keyup', (e) => { if (e.key === 'Alt') altDown = false; });
window.addEventListener('blur', () => { altDown = false; });
const wantsRepick = (ev) => !!(ev && ev.altKey) || altDown;

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
const NOTE_TTL = 8000;   // ms a note stays before clearing itself
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
// `doc` lets callers pass a CAPTURED document reference instead of re-reading app.activeDocument —
// needed by Square Art, whose crop dialog is a separate modal window that can steal "active
// document" status from Photoshop while it has focus (see onSquareArt).
const baseName = (doc) => ((doc || app.activeDocument).name || 'poster').replace(/\.(psd|psb|jpe?g|png|webp|gif|bmp|tiff?)$/i, '');

function updateBadge(style) {
  styleEl.textContent = style || '—';
  styleEl.className = 'style-name' + (style === 'MM2K' ? ' mm2k' : style === 'CL2K' ? ' cl2k' : '');
  // MM2K templates have text titles, no LOGO group → Place Logo is meaningless; hide it (Fit stays).
  logoBtn.classList.toggle('hidden', style === 'MM2K');
  logoExpBtn.classList.toggle('hidden', !curLogoGroup);   // Logo export needs an actual LOGO group
}

// ---- apply a changes list [{p,v}] to the real layers, then re-render ----
function applyChanges(changes) {
  return runExclusive(async () => {
    // Only write layers whose RAW visibility actually changes (rv) — a season click "hides" dozens of
    // already-hidden layers, and each write is a full executeAsModal round-trip. Paths without a model
    // node (ancestor-group reveals, seqGroup) always apply.
    const nodes = T.allNodes(model);
    const toApply = changes.filter((ch) => {
      const k = T.key(ch.p);
      const n = nodes.find((m) => T.key(m.p) === k);
      return !n || n.rv !== ch.v;
    });
    if (toApply.length) {
      await core.executeAsModal(async () => {
        for (const ch of toApply) { const L = layerByPath[T.key(ch.p)]; if (L) L.visible = ch.v; }
      }, { commandName: 'Toggle poster layers' });
    }
    changes.forEach((ch) => { const k = T.key(ch.p); nodes.forEach((n) => { if (T.key(n.p) === k) { n.v = ch.v; n.rv = ch.v; } }); });
    render();
  }).catch(showError);
}

const onSeason = (S_) => applyChanges(T.clickSeason(model, S_));
const onSingle = (SI) => applyChanges(T.clickSingle(model, SI));
const onSequel = (SQ) => applyChanges(T.clickSequel(model, SQ));
const onMain   = (Mn) => applyChanges(T.clickMain(model, Mn));
const onDecade = (D)  => applyChanges(T.clickDecade(model, D));
const onYear   = (Y)  => applyChanges(T.clickYear(model, Y));
const onYearsG = (YG) => applyChanges(T.clickYearsGroup(model, YG));

// ---- render ----
function chip(text, on, folder) {
  const c = document.createElement('div');
  c.className = 'chip ' + (on ? 'on' : 'off') + (folder ? ' grp' : '');
  c.textContent = text;
  return c;
}

function render() {
  singlesEl.innerHTML = '';
  model.singles.forEach((SI) => {
    const c = chip(SI.lab, SI.v, false); c.title = SI.n;
    c.addEventListener('click', () => onSingle(SI));
    singlesEl.appendChild(c);
  });
  singlesEl.classList.toggle('hidden', model.singles.length === 0);

  sequelsEl.innerHTML = '';
  const sqs = T.seqs(model);
  if (sqs.length) {
    const lbl = document.createElement('span'); lbl.className = 'rowlabel'; lbl.textContent = 'Sequel';
    sequelsEl.appendChild(lbl);
    sqs.forEach((SQ) => {
      const c = chip(M.seqLabel(SQ.n), SQ.v, false); c.classList.add('num'); c.title = SQ.n;
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
  let decadeOpen = true, yearsOpen = true;
  let row = null, rowCount = 0, rowRole = '';   // season chips flow in centered rows of 5, year chips (wider) in rows of 4
  model.seasons.forEach((N) => {
    if (N.r === 'other' || N.r === 'group' || N.r === 'otherLeaf') return;
    if (N.r === 'decade') decadeOpen = N.v;
    if (N.r === 'years') yearsOpen = N.v;
    if (N.r === 'season' && !decadeOpen) return;
    if (N.r === 'year' && !yearsOpen) return;
    const folder = (N.r === 'main' || N.r === 'decade' || N.r === 'years');
    const c = chip(M.label(N.n), N.v, folder); c.title = N.n;
    if (N.r === 'season')      { c.classList.add('num'); c.addEventListener('click', () => onSeason(N)); }
    else if (N.r === 'year')   c.addEventListener('click', () => onYear(N));
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
function refresh() {
  lastDocId = hasDoc() ? app.activeDocument.id : 0;
  if (!hasDoc()) {
    model = { singles: [], seasons: [], sequels: [], seqGroup: null }; layerByPath = {}; curStyle = ''; curLogoGroup = null;
    updateBadge(''); singlesEl.classList.add('hidden'); sequelsEl.classList.add('hidden');
    listEl.innerHTML = '<div class="msg">Open a poster PSD, then press ⟳.</div>';
    return;
  }
  try {
    const r = M.readModel(app.activeDocument, constants);
    model = r.model; layerByPath = r.layerByPath; curStyle = r.style; curLogoGroup = r.logoGroup;
    const ORD = { SP: 0, C: 1, CLS: 2 };
    model.singles.sort((a, b) => ORD[a.lab] - ORD[b.lab]);
    model.sequels.sort((a, b) => (parseInt(M.seqLabel(a.n), 10) || 0) - (parseInt(M.seqLabel(b.n), 10) || 0));
    updateBadge(curStyle);
    render();
  } catch (e) { showError(e); }
}

// ---- Save PSD (remote docs PUT back to the server; local docs overwrite the opened file) ----
async function onSave() {
  if (!hasDoc()) { note('No document open.'); return; }
  const ctx = remoteCtx();
  saveBtn.textContent = '…';
  try {
    if (ctx) { await runExclusive(() => S.savePsdRemote(app.activeDocument, ctx)); note('Saved "' + ctx.filename + '" back to Posterflow.'); }
    else { await runExclusive(() => S.savePsd(app.activeDocument)); }
    flash(saveBtn, '✓', '💾');
  } catch (e) { flash(saveBtn, '✗', '💾'); showError(e); }
}

// ---- Export JPG (remote docs upload to the server's image folder; local docs use the picked folder) ----
async function onJpg(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  const ctx = remoteCtx();
  jpgBtn.textContent = '…';
  try {
    const base = (ctx && ctx.name) || baseName();
    let res;
    if (ctx) {
      // Server upload first; a 400 means no image folder is configured there — fall back to the
      // panel's own remembered local folder (prompting once), so a blank server path never errors.
      try {
        res = await runExclusive(() => S.exportJpgRemote(app.activeDocument, ctx, base, M.activeSuffix(model)));
      } catch (e) {
        if (!/HTTP 400/.test(String(e && e.message))) throw e;
        note('Server has no image export folder — saving locally instead.');
        res = await runExclusive(() => S.exportJpg(app.activeDocument, ctx.style, base, M.activeSuffix(model), { forcePick: wantsRepick(ev) }));
      }
    } else {
      res = await runExclusive(() => S.exportJpg(app.activeDocument, curStyle, base, M.activeSuffix(model), { forcePick: wantsRepick(ev) }));
    }
    if (res.ok) { flash(jpgBtn, '✓', 'JPG'); note('Saved "' + res.filename + '" → ' + res.folderName); }
    else { jpgBtn.textContent = 'JPG'; if (res.reason === 'cancelled') note('JPG cancelled — no folder chosen.'); }
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

// ---- Export the LOGO group as a trimmed transparent PNG (Alt-click re-picks the folder).
// Remote docs render to a temp file and upload to the server's logo folder instead. ----
async function onLogoExport(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  if (!curLogoGroup) { note('No LOGO group in this document.'); return; }
  const ctx = remoteCtx();
  logoExpBtn.textContent = '…';
  try {
    const folder = ctx ? await R.tempFolder() : await FS.getLogoFolder({ forcePick: wantsRepick(ev) });
    if (!folder) { logoExpBtn.textContent = 'Logo'; note('Logo export cancelled — no folder chosen.'); return; }
    const filename = ((ctx && ctx.name) || baseName()) + '.png';
    const res = await runExclusive(() => TL.exportLogoPng(app.activeDocument, constants, folder, filename));
    if (res.ok && ctx) {
      const bytes = await R.readBytes(res.entry);
      try {
        await R.putBytes('/api/maker-tools/logo-exports/' + encodeURIComponent(res.filename), bytes);
        flash(logoExpBtn, '✓', 'Logo'); note('Saved "' + res.filename + '" → Posterflow logo folder.');
      } catch (e) {
        if (!/HTTP 400/.test(String(e && e.message))) throw e;
        // No logo folder configured server-side — fall back to the panel's local logo folder.
        note('Server has no logo export folder — saving locally instead.');
        const localFolder = await FS.getLogoFolder({});
        if (!localFolder) { logoExpBtn.textContent = 'Logo'; note('Logo export cancelled — no folder chosen.'); return; }
        await FS.writeFileBytes(localFolder, res.filename, bytes);
        flash(logoExpBtn, '✓', 'Logo'); note('Saved "' + res.filename + '" → ' + localFolder.name);
      }
    } else if (res.ok) {
      flash(logoExpBtn, '✓', 'Logo'); note('Saved "' + res.filename + '" → ' + res.folderName);
    } else {
      flash(logoExpBtn, '✗', 'Logo');
      note(res.reason === 'no-logo' ? 'No LOGO group in this document.' : res.reason === 'empty' ? 'LOGO group has no visible pixels.' : 'Logo export failed.');
    }
  } catch (e) { flash(logoExpBtn, '✗', 'Logo'); showError(e); }
}

// ---- Export a textless JPG: the PSD's POSTER group's currently-visible variant, with every other
// layer (logo, gradient, border, season/sequel text — anything outside POSTER) hidden. Alt-click
// re-picks the folder. Remote docs render to a temp file and upload to the server's textless
// folder instead. ----
async function onTextlessExport(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  const ctx = remoteCtx();
  textlessExpBtn.textContent = '…';
  try {
    const folder = ctx ? await R.tempFolder() : await FS.getTextlessFolder({ forcePick: wantsRepick(ev) });
    if (!folder) { textlessExpBtn.textContent = 'Textless'; note('Textless export cancelled — no folder chosen.'); return; }
    const filename = ((ctx && ctx.name) || baseName()) + M.activeSuffix(model) + '.jpg';
    const res = await runExclusive(() => TL.exportTextlessJpg(app.activeDocument, constants, folder, filename));
    if (res.ok && ctx) {
      const bytes = await R.readBytes(res.entry);
      try {
        await R.putBytes('/api/maker-tools/textless-exports/' + encodeURIComponent(res.filename), bytes);
        flash(textlessExpBtn, '✓', 'Textless'); note('Saved "' + res.filename + '" → Posterflow textless folder.');
      } catch (e) {
        if (!/HTTP 400/.test(String(e && e.message))) throw e;
        note('Server has no textless export folder — saving locally instead.');
        const localFolder = await FS.getTextlessFolder({});
        if (!localFolder) { textlessExpBtn.textContent = 'Textless'; note('Textless export cancelled — no folder chosen.'); return; }
        await FS.writeFileBytes(localFolder, res.filename, bytes);
        flash(textlessExpBtn, '✓', 'Textless'); note('Saved "' + res.filename + '" → ' + localFolder.name);
      }
    } else if (res.ok) {
      flash(textlessExpBtn, '✓', 'Textless');
      note('Saved "' + res.filename + '" → ' + res.folderName +
        (res.fallback ? ' (no POSTER group found — hid Logo/Gradient/Season/Sequel groups by name instead)' : ''));
    } else {
      flash(textlessExpBtn, '✗', 'Textless');
      note(res.reason === 'empty' ? 'The POSTER group has no visible variant.' : 'Textless export failed.');
    }
  } catch (e) { flash(textlessExpBtn, '✗', 'Textless'); showError(e); }
}

// ---- Square Art crop: isolate the POSTER group's active variant (same primitive as Textless
// above), let the user pick a square crop region (min 500x500, squarecrop.js), and save the cropped
// JPG. A downscaled preview renders first so the dialog opens fast; the actual crop always
// re-isolates and crops a FRESH full-resolution duplicate, never the preview.
// The crop dialog is a SEPARATE modal window (uxpShowModal) — while it has focus, Photoshop can
// stop reporting an "active document" at all, so app.activeDocument re-read at Save time resolves
// to something with no real id ("document with an id of undefined does not exist"). Capture the
// Document reference ONCE up front (targetDoc) and reuse it for every step, including inside the
// dialog's onSave callback, instead of ever re-reading app.activeDocument after the dialog opens. ----
let squareArtBusy = false;
async function onSquareArt(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  if (squareArtBusy) return;
  squareArtBusy = true;
  const targetDoc = app.activeDocument;
  const ctx = remoteCtx();
  squareArtBtn.textContent = '…';
  let previewUrl = null;
  const done = () => { squareArtBusy = false; if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; } };
  try {
    const sizeRes = await runExclusive(() => TL.readPosterSize(targetDoc, constants));
    if (!sizeRes.ok) {
      squareArtBtn.textContent = 'Square Art'; done();
      note(sizeRes.reason === 'empty' ? 'The POSTER group has no visible variant.' : 'Square Art failed.');
      return;
    }
    const tmp = await R.tempFolder();
    const prevRes = await runExclusive(() => TL.exportPosterPreview(targetDoc, constants, tmp, 'squareart-preview.jpg', 1400));
    if (!prevRes.ok) {
      squareArtBtn.textContent = 'Square Art'; done();
      note(prevRes.reason === 'empty' ? 'The POSTER group has no visible variant.' : 'Square Art failed.');
      return;
    }
    const bytes = await R.readBytes(prevRes.entry);
    previewUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));
    squareArtBtn.textContent = 'Square Art';

    SC.openSquareCropDialog({
      imageUrl: previewUrl,
      nativeW: sizeRes.width,
      nativeH: sizeRes.height,
      title: (ctx && ctx.name) || baseName(targetDoc),
      onCancel: done,
      onSave: async (cropNative) => {
        const folder = ctx ? await R.tempFolder() : await FS.getSquareartFolder({ forcePick: wantsRepick(ev) });
        if (!folder) throw new Error('no folder chosen');
        const filename = ((ctx && ctx.name) || baseName(targetDoc)) + M.activeSuffix(model) + ' - squareart.jpg';
        const res = await runExclusive(() => TL.exportSquareArtJpg(targetDoc, constants, folder, filename, cropNative));
        if (!res.ok) throw new Error(res.reason === 'empty' ? 'The POSTER group has no visible variant.' : 'Square Art export failed.');
        if (ctx) {
          const outBytes = await R.readBytes(res.entry);
          try {
            await R.putBytes('/api/maker-tools/squareart-exports/' + encodeURIComponent(res.filename), outBytes);
            note('Saved "' + res.filename + '" → Posterflow square art folder.');
          } catch (e) {
            if (!/HTTP 400/.test(String(e && e.message))) throw e;
            note('Server has no square art export folder — saving locally instead.');
            const localFolder = await FS.getSquareartFolder({});
            if (!localFolder) throw new Error('no folder chosen');
            await FS.writeFileBytes(localFolder, res.filename, outBytes);
            note('Saved "' + res.filename + '" → ' + localFolder.name);
          }
        } else {
          note('Saved "' + res.filename + '" → ' + res.folderName);
        }
        done();
      },
    });
  } catch (e) {
    squareArtBtn.textContent = 'Square Art';
    done();
    showError(e);
  }
}

// ---- Trim off-canvas pixels ----
async function onTrim() {
  if (!hasDoc()) { note('No document open.'); return; }
  trimBtn.textContent = '…';
  try { await runExclusive(() => TL.trim(app.activeDocument)); flash(trimBtn, '✓', 'Trim'); }
  catch (e) { flash(trimBtn, '✗', 'Trim'); showError(e); }
}

// ---- Batch export ----
const batchNote = (t) => { batchMsgEl.textContent = t; };
const modeBtn = () => batchMode === 'seasons' ? seasonsBtn : batchBtn;
const resetBolts = () => { batchBtn.textContent = '⚡'; seasonsBtn.textContent = '⚡'; };
const closeBatch = () => { if (!batchBusy) batchBar.classList.add('hidden'); };

function startBatch(mode) {
  if (batchBusy) return;
  batchMode = mode;
  batchBar.classList.remove('hidden'); batchRow.classList.add('hidden'); batchHelp.classList.add('hidden');
  batchNote('Scanning…');
  if (!hasDoc()) { batchNote('No document open.'); return; }
  refresh();   // rebuild model + layerByPath so batch changes apply to current layers
  if (batchMode === 'tags') {
    const base = baseName();
    const scan = M.scanBatch(app.activeDocument, constants, B.normName(base));
    const built = B.buildTagItems(scan.variants,
      { season: scan.seasonText, specials: scan.specialsText, cls: scan.clsText, collection: scan.collectionText }, base);
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

async function runBatch() {
  if (batchBusy) return;
  if (!batchConvention) {
    const r = B.parseRange(batchInput.value);
    batchItems = (B.isYearRange(r) && T.byRole(model, 'year').length) ? B.yearItems(model, r.start, r.end) : B.seasonItems(model, r.start, r.end);
  } else {
    const fk = B.parseTagFilter(batchInput.value);
    if (fk) batchItems = batchItems.filter((it) => fk[it.key]);
  }
  if (!batchItems.length) { batchNote('Nothing matched — nothing to export.'); return; }
  batchBusy = true; modeBtn().textContent = '…'; batchRow.classList.add('hidden');
  batchWarnings.forEach((w) => note('Batch: ' + w));
  const style = curStyle, batchCtx = remoteCtx(), base = (batchCtx && batchCtx.name) || baseName();
  let ok = 0;
  try {
    // Gradient safety net: turn a forgotten-off GRADIENT group on (and leave it on) before exporting.
    try {
      if (await runExclusive(() => TL.forceGradientVisible(app.activeDocument, constants)))
        note('The GRADIENT group had hidden layers — turned on and left visible in the PSD.');
    } catch (_) {}
    // Manual collection logos (c/c1/c2… in LOGO) stay hidden for non-collection exports.
    try { await runExclusive(() => TL.hideManualCollectionLogos(app.activeDocument, constants)); } catch (_) {}
    for (let i = 0; i < batchItems.length; i++) {
      batchNote('Exporting ' + (i + 1) + '/' + batchItems.length + '…');
      const changes = batchItems[i].changes;
      await runExclusive(() => core.executeAsModal(async () => {
        for (const ch of changes) { const L = layerByPath[T.key(ch.p)]; if (L) L.visible = ch.v; }
      }, { commandName: 'Batch variant ' + (i + 1) }));
      // Collection export: temporary logo copy on the collection ruler (removed right after).
      // Placement/cleanup are best-effort — any failure warns and the export proceeds bare.
      let placed = null;
      if (batchItems[i].collection) {
        try { placed = await runExclusive(() => TL.placeCollectionLogo(app.activeDocument, constants)); }
        catch (e) { placed = null; note('Batch: collection logo placement failed (' + (e && e.message ? e.message : e) + ') — exported without repositioning the logo.'); }
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
            if (i === 0) note('Server has no image export folder — saving batch locally instead.');
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
      else if (res.reason === 'cancelled') { batchNote('Stopped — no folder chosen.'); batchBusy = false; resetBolts(); return; }
    }
    batchNote('Done — exported ' + ok + ' JPG' + (ok === 1 ? '' : 's') + '.');
  } catch (e) {
    batchNote('Stopped: ' + (e && e.message ? e.message : e));
  } finally {
    batchBusy = false; resetBolts(); refresh(); setTimeout(closeBatch, 2600);
  }
}

// Each bolt toggles ITS mode: click the same bolt again to dismiss; click the other to switch modes.
function boltClick(mode) {
  if (batchBusy) return;
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
textlessExpBtn.addEventListener('click', onTextlessExport);
squareArtBtn.addEventListener('click', onSquareArt);
document.querySelector('[data-act="folders"]').addEventListener('click', () => {
  FS.clearAllFolders();
  note('Remembered folders cleared — the next JPG / Logo / Textless / Square Art export will ask again.');
});
document.querySelector('[data-act="refresh"]').addEventListener('click', refresh);
batchBtn.addEventListener('click', () => boltClick('tags'));
seasonsBtn.addEventListener('click', () => boltClick('seasons'));
document.querySelector('[data-act="batch-run"]').addEventListener('click', runBatch);
document.querySelector('[data-act="batch-help"]').addEventListener('click', () => {
  const rows = batchMode === 'tags'
    ? [['blank', 'run everything'], ['c', 'Collection'], ['cls', 'Limited Series'], ['main / show', 'main poster'],
       ['s3 or 3', 'one season'], ['0', 'Specials'], ['s1-8', 'a season range'], ['main, c', 'combine with commas']]
    : [['blank', 'all seasons + Specials'], ['8', 'seasons 1-8'], ['3-6', 'that range'], ['0-5', 'includes Specials'], ['2015-2020', 'year layers']];
  batchHelp.innerHTML = rows.map((r) => '<div><b>' + r[0] + '</b> — ' + r[1] + '</div>').join('');
  batchHelp.classList.toggle('hidden');
});
document.querySelector('[data-act="batch-cancel"]').addEventListener('click', closeBatch);
batchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runBatch(); } });

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
    linkMsg.textContent = 'Posterflow server link is off. Enter the server URL (and app password if one is set), Save, then Enable.';
  } else if (!linkStatus) {
    linkMsg.textContent = 'Connecting to ' + R.baseUrl(cfg) + '…';
  } else if (linkStatus.ok) {
    linkMsg.textContent = 'Connected to ' + R.baseUrl(cfg) + ' — "PS" exports open here automatically.';
  } else {
    linkMsg.textContent = 'Can\'t reach ' + R.baseUrl(cfg) + ' — ' + linkStatus.msg + '. (Plain http works on Windows only; Mac needs https.)';
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
  note(cfg.enabled ? 'Posterflow link enabled — watching the queue.' : 'Posterflow link disabled.');
});

// Poll the server queue and open claimed PSDs. Skipped while any operation is running; errors are
// throttled to one note per streak so a down server doesn't spam.
let pollErrNoted = false;
setInterval(async () => {
  if (busy || batchBusy || squareArtBusy) return;
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
// Also skipped while squareArtBusy: the Square Art crop dialog runs with lockDocumentFocus:true
// (needed so its own Save can touch the document at all — see squarecrop.js), and while that lock
// is held, ANY other code touching app.documents/app.activeDocument from outside the dialog's own
// flow throws ("Cannot read properties of undefined (reading 'batchPlay')") instead of just
// returning a stale value.
let rescanT = null;
const scheduleRescan = () => {
  if (rescanT) clearTimeout(rescanT);
  rescanT = setTimeout(() => { rescanT = null; if (!busy && !batchBusy && !squareArtBusy) refresh(); }, 500);
};
try {
  require('photoshop').action.addNotificationListener(['show', 'hide', 'open', 'close'], scheduleRescan);
} catch (e) { console.log('Posterflow: event listener unavailable, relying on poll only.', e); }
setInterval(() => {
  if (busy || batchBusy || squareArtBusy) return;
  const id = hasDoc() ? app.activeDocument.id : 0;
  if (id !== lastDocId) refresh();
}, 2000);

refresh();   // read whatever's open when the panel mounts
