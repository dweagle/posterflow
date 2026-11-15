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
const cmBar       = document.querySelector('.cmbar');
const editBtn       = document.querySelector('[data-act="edit"]');
const sendBackBtn   = document.querySelector('[data-act="send-back"]');
const editCancelBtn = document.querySelector('[data-act="edit-cancel"]');
const saveBgBtn     = document.querySelector('[data-act="save-bg"]');
const uploadBtn   = document.querySelector('[data-act="upload"]');
const completeBtn = document.querySelector('[data-act="complete"]');
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
// A failure: a red note that stays until its ✕ is pressed. Newest first; past MAX_ALERTS the oldest go.
const MAX_ALERTS = 5;
const fail = (t) => {
  const d = document.createElement('div');
  d.className = 'alert';
  const text = document.createElement('div');
  text.className = 'alerttext'; text.textContent = t;
  const x = document.createElement('sp-action-button');
  x.setAttribute('size', 's'); x.setAttribute('title', 'Dismiss'); x.textContent = '\u2715';
  x.addEventListener('click', () => { if (d.parentNode) d.parentNode.removeChild(d); });
  d.appendChild(text); d.appendChild(x);
  notesEl.insertBefore(d, notesEl.firstChild);
  const all = notesEl.querySelectorAll('.alert');
  for (let i = MAX_ALERTS; i < all.length; i++) notesEl.removeChild(all[i]);
  return d;
};
const showError = (e) => fail('Error: ' + (e && e.message ? e.message : e));
FS.setOnMissing((what) => note('The remembered ' + what + ' can\'t be reached. Pick it again, or cancel and retry when the share is back.'));
// Folder ask: the bar at the top names the folder, then Choose opens its picker. One ask at a time;
// a second waits for the first to be answered.
const askBar = document.querySelector('.askbar');
let askChain = Promise.resolve(), askAnswer = null;
FS.setAsker((what) => {
  const next = askChain.then(() => new Promise((resolve) => {
    document.querySelector('.askmsg').textContent = 'Choose your ' + what.name;
    document.querySelector('.askhint').textContent = what.hint;
    askBar.classList.remove('hidden');
    askAnswer = (yes) => { askAnswer = null; askBar.classList.add('hidden'); resolve(yes); };
  }));
  askChain = next;
  return next;
});
document.querySelector('[data-act="ask-choose"]').addEventListener('click', () => { if (askAnswer) askAnswer(true); });
document.querySelector('[data-act="ask-cancel"]').addEventListener('click', () => { if (askAnswer) askAnswer(false); });
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
// Chip text only: the model, toggles and batch keep keying off SP / C / CLS.
const SINGLE_TEXT = { SP: 'SPECIALS', C: 'COLLECTION', CLS: 'LIMITED SERIES' };
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
  // Row 1: MOVIE · SHOW · COLLECTION, blue, each with a right-click finish. Row 2: the SPECIALS / LIMITED SERIES toggles.
  const finRow = document.createElement('div'); finRow.className = 'tagrow';
  const togRow = document.createElement('div'); togRow.className = 'tagrow';
  MAIN_TAGS.forEach((T_) => {
    const c = chip(T_.lab, false, false);
    c.classList.add('tagonly');
    tip(c, 'Clear all season / Specials / Collection / Limited Series layers.  \u00b7  '
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
    finRow.appendChild(c);
  });
  // COLLECTION keeps its toggle (a plain click shows / hides the COLLECTION text) and adds the finish.
  const CS = model.singles.find((SI) => SI.lab === 'C') || null;
  const cc = chip(SINGLE_TEXT.C, !!(CS && CS.v), false);
  cc.classList.add('tagonly');
  tip(cc, (CS ? tagTitle(CS.n, 'c') : 'No COLLECTION text layer in this document  \u00b7  Alt-click: rename the selected layer to "c"')
          + '  \u00b7  ' + finishTip('c'));
  cc.addEventListener('click', (ev) => {
    if (wantsRepick(ev)) renameSelectedTo('c');
    else if (CS) onSingle(CS);
    else note('No COLLECTION text layer in this document.');
  });
  cc.addEventListener('contextmenu', (ev) => {
    ev.preventDefault();
    alt.release();
    onFinishCollection();
  });
  finRow.appendChild(cc);
  model.singles.forEach((SI) => {
    if (SI.lab === 'C') return;   // rendered above, in the finish row
    const c = chip(SINGLE_TEXT[SI.lab] || SI.lab, SI.v, false);
    const tag = SINGLE_TAG[SI.lab] || null;
    tip(c, (tag ? tagTitle(SI.n, tag) : SI.n) + (SI.lab === 'CLS' ? '  \u00b7  ' + finishTip('cls') : ''));
    c.addEventListener('click', (ev) => {
      if (tag && wantsRepick(ev)) renameSelectedTo(tag); else onSingle(SI);
    });
    if (SI.lab === 'CLS') c.addEventListener('contextmenu', (ev) => { ev.preventDefault(); alt.release(); onFinishCls(); });
    togRow.appendChild(c);
  });
  singlesEl.appendChild(finRow);
  if (togRow.childNodes.length) singlesEl.appendChild(togRow);
  // The finish chips are always available, so the rows show even with no SP / C / CLS layers.
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
  else keepFinishJpg(res);
  return res;
}
// A manual export (the JPG button, a ⚡ Run) checks its poster layers first (TL.checkCoverage). Blank
// canvas holds it back once: the same press again within HOLD_MS goes ahead.
const HOLD_MS = 10000;
// A question the next press answers: a blue note that goes after HOLD_MS.
const ask = (t) => {
  const d = document.createElement('div');
  d.className = 'msg ask'; d.textContent = t;
  notesEl.insertBefore(d, notesEl.firstChild);
  setTimeout(() => { if (d.parentNode) d.parentNode.removeChild(d); }, HOLD_MS);
  return d;
};
// `show` writes the held press's note (a red one unless given).
function makeHold(show) {
  let h = { key: '', until: 0, alert: null, data: null };
  return {
    // What a held press kept (true unless set() was given more) when this press repeats it in time, else
    // false. Either way the old note goes and the hold is spent.
    again(key) {
      const kept = h.key === key && Date.now() < h.until ? h.data : false;
      if (h.alert && h.alert.parentNode) h.alert.parentNode.removeChild(h.alert);
      h = { key: '', until: 0, alert: null, data: null };
      return kept;
    },
    set(key, text, data) { h = { key, until: Date.now() + HOLD_MS, alert: (show || fail)(text), data: data || true }; },
  };
}
const jpgHold = makeHold(), batchHold = makeHold();
// The blank canvas in getTargets()'s layers as text, or '' when there is none or nothing to check.
async function blankCanvas(getTargets) {
  try {
    if (!rootPosterGroup()) return '';
    const targets = await getTargets();
    const r = await runExclusive(() => TL.checkCoverage(app.activeDocument, constants, targets));
    return r.skipped ? '' : r.problems.join('; ');
  } catch (_) { return ''; }   // a check that breaks never blocks a manual export
}
async function onJpg(ev) {
  if (!hasDoc()) { note('No document open.'); return; }
  const repick = wantsRepick(ev);
  jpgBtn.textContent = '…';
  try {
    const key = String(app.activeDocument.id);
    const blank = jpgHold.again(key) ? '' : await blankCanvas(visibleTarget);
    if (blank) {
      jpgHold.set(key, 'JPG not exported. ' + blank + '. Press JPG again to export anyway.');
      flash(jpgBtn, '\u2717', 'JPG');
      return;
    }
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
// Find the LOGO group LIVE (the cached layerByPath snapshot can go stale), top level only like POSTER:
// every name / kind read is a host round-trip, and descending crawled SEASONS' ~130 layers first (seconds, panel frozen).
function findLogoGroupLive(layers) {
  for (let i = 0; i < layers.length; i++) {
    const L = layers[i];
    if (/^\s*logos?\s*$/i.test(L.name) && L.kind === constants.LayerKind.GROUP) return L;
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
      const hint = res.reason === 'no-logo' ? 'No LOGO group in this document.' : res.reason === 'empty' ? 'LOGO group has no visible pixels.' : '';
      if (hint) note(hint); else fail('Logo export failed.');
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
      if (res.reason === 'no-layer') note('Select the poster layer(s) first, then press Poster.'); else fail('Poster export failed.');
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
let sqLast = null;   // the last valid square {x,y,side}: a marquee click without a drag is Deselect, so the keeper puts it back
async function sqKeepInside() {
  if (!sqArmed || !sqTemp || sqBusy || sqKeepBusy) return;
  sqKeepBusy = true;
  try {
    if (!hasDoc() || app.activeDocument.id !== sqTemp.doc.id) return;
    const fix = await runExclusive(async () => {
      const s = await TL.readSelectionBounds(sqTemp.doc);
      if (!s) {   // deselected: restore the square as it was last positioned
        if (!sqLast) return null;
        await TL.setSquareSelection(sqTemp.doc, sqLast.x, sqLast.y, sqLast.side);
        return Object.assign({ restored: true }, sqLast);
      }
      const f = G.keepSquareInside(s);
      if (f) { await TL.setSquareSelection(sqTemp.doc, f.x, f.y, f.side); sqLast = { x: f.x, y: f.y, side: f.side }; return f; }
      const kept = G.squareOnCanvas(s);   // a square sitting on the canvas, including one just drawn, is the one to restore
      if (kept) sqLast = kept;
      return null;
    });
    if (fix && fix.restored) note('Restored the ' + fix.side + '×' + fix.side + ' selection (a click without dragging deselects).');
    else if (fix) note('Moved the ' + fix.side + '×' + fix.side + ' selection back onto the canvas.');
  } catch (_) {
  } finally { sqKeepBusy = false; }
}
const sqKeepSoon = () => {
  if (!sqArmed) return;
  if (sqKeepT) clearTimeout(sqKeepT);
  sqKeepT = setTimeout(() => { sqKeepT = null; sqKeepInside(); }, 120);
};
const SQ_EVENTS = ['toolModalStateChanged', 'historyStateChanged', 'set'];   // listened to only while armed

// The Upload / Complete bar shows only while the Posterflow link is on.
function updateCommunityBar() { cmBar.classList.toggle('hidden', !R.isConfigured(R.getConfig())); }
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
  squareArtBtn.classList.toggle('crop', armed);   // green while armed: this press exports
  updateEditUI();
}
async function sqCleanup() {   // close the crop tab, return home, hand the Move tool back
  const t = sqTemp; sqTemp = null;
  if (t) await runExclusive(() => TL.closeCropDoc(t.doc, sqHome));
  sqHome = null; sqCtx = null; sqWant = 0; sqLast = null;
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
        const hint = res.reason === 'noposter' ? 'No POSTER group in this document.' : res.reason === 'empty' ? 'The POSTER group has no visible variant.' : '';
        if (hint) note(hint); else fail('Square Art failed.');
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
      sqLast = { x, y, side };
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
    if (!res.ok) { flashSq('✗'); fail('Square Art export failed.'); await sqCleanup(); return; }
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
    if (check) { sqLast = { x, y, side: s }; note('Placed a ' + s + '×' + s + ' selection. Drag inside it to position, then press Crop.'); }
    else fail('Could not place the selection. Drag one with the marquee tool instead.');
  } catch (e) { showError(e); }
}
async function onSqCancel() {
  if (sqBusy) return;
  sqSetUI(false);
  await sqCleanup();
}

// ---- Finish (right-click MOVIE / SHOW / COLLECTION): tidy the layers, save, export, hand off to Square Art ----
// MOVIE:            clear → POSTER layer → "main" → LOGO layer → "Title (Year) - Logo" → [trim] → SAVE
//                   → JPG → logo PNG → Square Art
// SHOW (orange ⚡):  clear → logo name → [trim] → SAVE → tag check → tag batch, unattended
//                   → logo PNG → show poster only → SAVE again → Square Art
// SHOW (blue ⚡):    same up to SAVE, then the range box waits for Run; the tail resumes from the logo PNG.
// COLLECTION:       POSTER layer → "c" → logo name → [trim] → SAVE → JPG through the tag batch filtered to "c"
//                   → logo PNG → SAVE again → Square Art
// Prep failures are recorded but never skip the save; after a failed prep step nothing exports (a
// stray visible season layer would misname the JPG). One checklist note reports every run.
let finishBusy = false;   // a pipeline is running, or the blue path is waiting on the range box
let finishWait = null;    // a SHOW finish waiting for Run (blue, or orange with one poster): { docId, resume(result) } until Run / ✕ / a bolt click
// ⚙ finish steps: every step of a right-click finish can be unticked; the order never changes.
// Every step is on by default, except the three that reach outside this computer (upload, backdrop, complete).
const STEP_DEFAULTS = {
  movie: { clear: true, main: true, logoName: true, trim: true, save: true, coverage: true, jpg: true, logoPng: true, upload: false, backdrop: false, complete: false, squareArt: true },
  show:  { clear: true, logoName: true, trim: true, save: true, tagCheck: true, coverage: true, batch: true, logoPng: true, showOnly: true, saveAgain: true, upload: false, backdrop: false, complete: false, squareArt: true },
  collection: { c: true, logoName: true, trim: true, save: true, coverage: true, jpg: true, logoPng: true, saveAgain: true, upload: false, backdrop: false, complete: false, squareArt: true },
};
const UPLOAD_WHY = 'Upload the JPGs this finish exported for your claimed request, as the Upload button in Posterflow is set: to its Discord thread, to IDarr, or both. Only the posters the request asks for are sent. Needs the Posterflow link and Discord connected in Posterflow. Skipped when no claimed request matches the PSD';
const BACKDROP_WHY = 'Check that this title\'s background is saved in Posterflow before the request is completed. Not saved yet: the complete waits, a note says so, and the request is completed as soon as "<PSD name> - background.jpg" is saved. Complete now on the note is for titles with no backdrop. A wait gives up after 3 days and leaves the request open. With no request to complete, the note is a reminder that clears when the background is saved';
const COMPLETE_WHY = 'Mark your claimed request complete in Posterflow, or your claimed list item when there is no request. A request is completed only after its posters were uploaded';
const COVERAGE_WHY = 'Check that every poster this finish exports covers the canvas inside the border, down to the lowest horizontal guide. Blank canvas stops the finish before the exports. The PSD is still saved';
const STEP_LABELS = {   // [key, chip text, what it does] in pipeline order
  movie: [
    ['clear', 'clear', 'Clear all season / Specials / Collection / Limited Series layers first: a stray visible season layer would misname the JPG'],
    ['main', 'main', 'Rename the visible POSTER layer to "main"'],
    ['logoName', 'logo name', 'Rename the visible LOGO layer to "Title (Year) - Logo"'],
    ['trim', 'trim', 'Trim off-canvas pixels: they are deleted, and the PSD saves right after'],
    ['save', 'save', 'Save the PSD: a failed save stops the finish'],
    ['coverage', 'coverage', COVERAGE_WHY],
    ['jpg', 'JPG', 'Export the flattened JPG'],
    ['logoPng', 'logo PNG', 'Export the LOGO group as a transparent PNG'],
    ['upload', 'upload', UPLOAD_WHY],
    ['backdrop', 'backdrop', BACKDROP_WHY],
    ['complete', 'complete', COMPLETE_WHY],
    ['squareArt', 'Square Art', 'Open Square Art with the default square placed'],
  ],
  show: [
    ['clear', 'clear', 'Clear all season / Specials / Collection / Limited Series layers first'],
    ['logoName', 'logo name', 'Rename the visible LOGO layer to "Title (Year) - Logo"'],
    ['trim', 'trim', 'Trim off-canvas pixels: they are deleted, and the PSD saves right after'],
    ['save', 'save', 'Save the PSD before the batch: a failed save stops the finish'],
    ['tagCheck', 'tag check', 'Check the tags before the batch: any problem stops the finish (orange only)'],
    ['coverage', 'coverage', COVERAGE_WHY],
    ['batch', 'batch', 'Run the batch (orange: every tag, unattended, or a single show poster and the seasons you type; blue: the seasons you type)'],
    ['logoPng', 'logo PNG', 'Export the LOGO group as a transparent PNG'],
    ['showOnly', 'show only', 'Hide the season text and show only the show-tagged poster'],
    ['saveAgain', 'save again', 'Save the PSD again so it closes without a prompt'],
    ['upload', 'upload', UPLOAD_WHY],
    ['backdrop', 'backdrop', BACKDROP_WHY],
    ['complete', 'complete', COMPLETE_WHY],
    ['squareArt', 'Square Art', 'Open Square Art with the default square placed'],
  ],
  collection: [
    ['c', 'c', 'Rename the visible POSTER layer to "c"'],
    ['logoName', 'logo name', 'Rename the visible LOGO layer to "Title (Year) - Logo"'],
    ['trim', 'trim', 'Trim off-canvas pixels: they are deleted, and the PSD saves right after'],
    ['save', 'save', 'Save the PSD: a failed save stops the finish'],
    ['coverage', 'coverage', COVERAGE_WHY],
    ['jpg', 'JPG', 'Export the Collection JPG through the tag batch: the logo moves to the collection ruler and the file is named like the batch names it'],
    ['logoPng', 'logo PNG', 'Export the LOGO group as a transparent PNG'],
    ['saveAgain', 'save again', 'Save the PSD again so it closes without a prompt: the export toggles layers after the first save'],
    ['upload', 'upload', UPLOAD_WHY],
    ['backdrop', 'backdrop', BACKDROP_WHY],
    ['complete', 'complete', COMPLETE_WHY],
    ['squareArt', 'Square Art', 'Open Square Art with the default square placed'],
  ],
};
const FINISHES = ['movie', 'show', 'collection'];
function loadSteps() {
  const out = {};
  FINISHES.forEach((f) => { out[f] = Object.assign({}, STEP_DEFAULTS[f]); });
  try {
    const saved = JSON.parse(localStorage.getItem('posterflow.finishSteps') || '{}');
    if (saved.v !== 2) return out;   // choices stored before every step defaulted on are dropped
    FINISHES.forEach((f) => Object.keys(out[f]).forEach((k) => { if (saved[f] && typeof saved[f][k] === 'boolean') out[f][k] = saved[f][k]; }));
  } catch (_) {}
  return out;
}
const steps = loadSteps();
const optBar = document.querySelector('.optbar');
function renderOptions() {   // one toggle chip per step, green = runs, grey = skipped
  FINISHES.forEach((f) => {
    const row = document.querySelector('.optrow[data-steps="' + f + '"]');
    row.innerHTML = '';
    STEP_LABELS[f].forEach(([k, text, why]) => {
      const c = chip(text, steps[f][k], false);
      tip(c, why);
      c.addEventListener('click', () => {
        steps[f][k] = !steps[f][k];
        try { localStorage.setItem('posterflow.finishSteps', JSON.stringify(Object.assign({ v: 2 }, steps))); } catch (_) {}
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
  : tag === 'c'
  ? 'Right-click: finish the collection poster: ' + stepList('collection')
  : tag === 'cls'
  ? 'Right-click: finish the limited series: the main JPG and " - Season 1" with this text. One poster makes both. A show layer and a cls layer export as tagged. Season layers stop it. Steps: ' + stepList('show')
  : 'Right-click: finish the show with the orange \u26a1 (unattended; a single show poster waits for its seasons, blank = main + Season 1)  \u00b7  Shift+right-click: with the blue \u26a1 (you type the seasons).  Steps: ' + stepList('show');
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
// docId is the PSD the finish is for: once another document is active, no later step runs. Without
// one (the community steps, which touch no document) the checklist is not tied to a PSD.
function finishRun(label, docId) {
  const parts = [], failed = [];
  let stopped = false;
  // True once another document is active (and from then on); the first call records why.
  const switched = (name) => {
    if (docId == null || (!stopped && hasDoc() && app.activeDocument.id === docId)) return false;
    if (!stopped) { stopped = true; failed.push(name); parts.push('\u2717 ' + name + ': another document became active, so the finish stopped'); }
    return true;
  };
  const step = async (name, fn) => {
    if (switched(name)) return false;
    try {
      const r = await fn();
      parts.push(r && r.skipped ? '\u2013 ' + name + ' skipped (' + r.skipped + ')' : '\u2713 ' + name + (r && r.info ? ' (' + r.info + ')' : ''));
      return true;
    } catch (e) { parts.push('\u2717 ' + name + ': ' + (e && e.message ? e.message : e)); failed.push(name); return false; }
  };
  const report = (tail) => {
    const t = label + ': ' + parts.concat(tail ? [tail] : []).join('  \u00b7  ');
    if (failed.length || /^STOPPED/.test(tail || '')) fail(t); else note(t);
  };
  return { step, report, failed, switched };
}

const stepClear = () => applyChangesCore(T.clearAll(model));
const stepMain = () => renameVisibleIn(rootPosterGroup(), 'POSTER', 'main', true);
// MM2K has text titles and no LOGO group, so its logo steps skip; any other template without one FAILS
// (the finish used to carry on and quietly produce a poster with no logo PNG).
const logoGroupForFinish = () => {
  const grp = findLogoGroupLive(app.activeDocument.layers);
  if (!grp && curStyle !== 'MM2K') throw new Error('no LOGO group at the top level');
  return grp;
};
const stepLogoName = async () => {
  const grp = logoGroupForFinish();
  if (!grp) return { skipped: 'no LOGO group on MM2K' };
  await renameVisibleIn(grp, 'LOGO', logoLayerName(), false);
};
const stepTrim = () => runExclusive(() => TL.trim(app.activeDocument));
const stepJpg = async () => { const r = await jpgCore(false); if (!r.ok) throw new Error(r.msg); };
const stepLogoPng = async () => {
  if (!logoGroupForFinish()) return { skipped: 'no LOGO group on MM2K' };
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

// ---- Blank-canvas check (TL.checkCoverage): the poster layers this finish is about to export ----
// MOVIE and blue SHOW export what POSTER shows: its topmost visible layer.
function visibleTarget() {
  const L = Array.from(rootPosterGroup().layers || []).find((K) => K.visible);
  return [{ label: L ? L.name : 'POSTER', layer: L || null, why: 'no visible layer' }];
}
// Tagged exports (orange SHOW, COLLECTION, an orange ⚡ Run): every tagged POSTER layer, or those `pick` keeps.
async function taggedTargets(pick) {
  await refresh();   // the prep steps renamed layers; read the tags as they are now
  const scan = M.scanBatch(lastTree || app.activeDocument, constants, B.normName(baseName()));
  const kids = Array.from(rootPosterGroup().layers || []), ids = kids.map((K) => K.id);
  return scan.variants.filter((v) => !pick || pick(v)).map((v) => {
    const n = layerByPath[T.key(v.p)];
    return { label: v.nm, layer: (n && kids[ids.indexOf(n.id)]) || null };
  });
}
const stepCoverage = async (targets) => {
  const r = await runExclusive(() => TL.checkCoverage(app.activeDocument, constants, targets));
  if (r.skipped) return { skipped: r.skipped };
  if (r.problems.length) throw new Error(r.problems.join('; '));
  return r.boundsOnly.length ? { info: 'bounds only: ' + r.boundsOnly.join(', ') } : undefined;
};

// ---- Community: upload this finish's JPGs for the claimed request, then complete it ----
// An upload does what the Upload button in Posterflow is set to do (Discord, IDarr, or both), and
// sends only the posters the request asks for. Everything goes through the Posterflow link (R.api):
// the panel holds nothing from Discord or the community service. The steps run after the exports,
// in the background, so Square Art opens at once.
const COMMUNITY = '/api/maker-tools/community/';
let finishX = null;   // the running finish: { kind, docId, name, jpgs: [{ name, file }], uploaded }
const communityOn = (f) => steps[f].upload || steps[f].backdrop || steps[f].complete;
// The name the active PSD's exports carry (a Posterflow PSD keeps the name it was queued with).
const exportBase = () => { const ctx = remoteCtx(); return (ctx && ctx.name) || baseName(); };
function startFinishX(kind) { finishX = { kind, docId: app.activeDocument.id, name: exportBase(), jpgs: [], uploaded: false, held: false }; }
// A JPG the running finish exported from its own PSD; a re-export of the same name replaces the earlier one.
function keepFinishJpg(res) {
  const X = finishX;
  if (!finishBusy || !X || !res.file || !hasDoc() || app.activeDocument.id !== X.docId) return;
  X.jpgs = X.jpgs.filter((j) => j.name !== res.filename).concat([{ name: res.filename, file: res.file }]);
}
// What Posterflow's answer about the claim means for a step: '' to go on, else why it is skipped. Throws when it must stop.
function claimSkip(c, X) {
  if (c.status === 'match') return '';
  if (c.status === 'not_connected') return 'Discord is not connected in Posterflow';
  if (c.status === 'no_id') return 'the PSD name has no {tmdb-…} id';
  if (c.status === 'none') return 'none of your claimed requests matches "' + cleanTitle(X.name) + '"';
  if (c.status === 'done') return 'already completed: ' + c.label;
  if (c.status === 'expired') throw new Error('the Discord login in Posterflow has expired. Reconnect Discord there');
  if (c.status === 'several') throw new Error(c.labels.length + ' claimed ' + (c.type === 'list_item' ? 'list items' : 'requests') + ' match (' + c.labels.join(', ') + '). Finish this one in Posterflow');
  throw new Error('unexpected answer from Posterflow');
}
const stepUpload = async (X, c) => {
  const skip = claimSkip(c, X);
  if (skip) return { skipped: skip };
  if (c.upload.problem) return { skipped: c.upload.problem };
  if (!X.jpgs.length) return { skipped: 'no JPGs exported in this finish' };
  const at = COMMUNITY + c.where + '/' + c.id + '/posters';
  for (const j of X.jpgs) await R.api('PUT', at + '/' + encodeURIComponent(j.name), await R.readBytes(j.file));
  const r = await R.api('POST', at);   // Posterflow uploads only the posters the request asks for
  if (!r.posted && !r.idarr) return { skipped: c.label + ' asks for none of the ' + jpgs(X.jpgs.length) + ' this finish exported' };
  X.uploaded = true;
  return { info: r.did + (r.skipped.length ? '; ' + r.skipped.length + ' not requested' : '') };
};
const stepComplete = async (X, c, uploadOn) => {
  const skip = claimSkip(c, X);
  if (skip) return { skipped: skip };
  if (uploadOn && !X.uploaded && c.type !== 'list_item') return { skipped: 'nothing was uploaded' };   // a list item needs no upload
  await R.api('POST', COMMUNITY + c.where + '/' + c.id + '/complete');
  return { info: c.label };
};
// The gear's backdrop step. The background is saved: the complete runs now. Not saved: a claimed request
// (or list item) that would have been completed waits for it instead; with nothing to complete, the
// note is only a reminder. Either way the wait is saved, so it outlasts a restart.
const backgroundName = (name) => name + ' - background.jpg';
const backgroundSaved = async (file) => !!(await R.api('GET', '/api/maker-tools/artwork-exports/background/' + encodeURIComponent(file))).exists;
const stepBackdrop = async (X, c, S_) => {
  const file = backgroundName(X.name);
  if (await backgroundSaved(file)) return { info: 'already saved' };
  if (S_.complete && c && c.status === 'match' && (!S_.upload || X.uploaded || c.type === 'list_item')) {
    addWait({ id: c.id, at: Date.now(), where: c.where, kind: X.kind, name: X.name, label: c.label, file });
    X.held = true;
    return { info: 'not saved yet' };
  }
  addWait({ id: 'file:' + file, at: Date.now(), remind: true, label: cleanTitle(X.name), file });
  return { info: 'not saved yet, reminder added' };
};
// Upload, backdrop, then complete (only if the upload did not fail), reported in a note of their own.
function finishCommunity(f) {
  const S_ = steps[f], X = finishX;
  if (!X || !communityOn(f)) return;
  const F = finishRun(f.toUpperCase() + ' · ' + cleanTitle(X.name));
  const linked = R.isConfigured(R.getConfig()), noLink = { skipped: 'no Posterflow link in 🔗' };
  let c = null;
  const claim = async () => { if (!c) c = await R.api('GET', COMMUNITY + 'claim?kind=' + X.kind + '&name=' + encodeURIComponent(X.name)); return c; };
  const claimIfAny = async () => { try { return await claim(); } catch (_) { return null; } };
  (async () => {
    if (S_.upload && !(await F.step('upload', async () => (linked ? stepUpload(X, await claim()) : noLink)))) { F.report(skippedTail(f, ['backdrop', 'complete'])); return; }
    if (S_.backdrop) {
      if (!(await F.step('backdrop', async () => (linked ? stepBackdrop(X, S_.complete ? await claimIfAny() : null, S_) : noLink)))) { F.report(skippedTail(f, ['complete'])); return; }
      if (X.held) { F.report('complete waits for the backdrop'); return; }
    }
    if (S_.complete) await F.step('complete', async () => (linked ? stepComplete(X, await claim(), S_.upload) : noLink));
    F.report();
  })().catch(showError);
}

// ---- Requests waiting for their backdrop: an amber note each, checked every few seconds ----
// waits: [{ id, at, file, label, remind } | { id, at, file, label, where, kind, name }], saved in
// localStorage. A request's wait completes it when the background is saved; a reminder just clears.
const WAITS_KEY = 'posterflow.backdropWaits';
const WAIT_CHECK_MS = 3000;
const WAIT_DAYS = 3;   // a wait gives up after this long, so none sits here forever
const waitsEl = document.querySelector('.waits');
const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
// A call that never answers would leave a wait busy for good: give up on it after 20 s.
const within = (p, what) => Promise.race([p, new Promise((_, no) => setTimeout(() => no(new Error(what + ' timed out after 20 s')), 20000))]);
function loadWaits() { try { const w = JSON.parse(localStorage.getItem(WAITS_KEY) || '[]'); return Array.isArray(w) ? w : []; } catch (_) { return []; } }
let waits = loadWaits();
const waitStatus = {};        // wait id -> its status line, set in place so the buttons never re-render under a click
const waitBusy = new Set();   // wait ids being completed right now
function setWaitStatus(id, text) {
  waitStatus[id] = text;
  const el = Array.from(waitsEl.querySelectorAll('.waitstatus')).find((e) => e.getAttribute('data-wait') === id);
  if (el) el.textContent = text;
}
function saveWaits() { try { localStorage.setItem(WAITS_KEY, JSON.stringify(waits)); } catch (_) {} renderWaits(); }
function addWait(w) { waits = waits.filter((o) => o.id !== w.id).concat([w]); saveWaits(); }
function dropWait(id) { waits = waits.filter((o) => o.id !== id); delete waitStatus[id]; saveWaits(); }
function renderWaits() {   // per wait: its title and X, then its status line and Complete now
  waitsEl.innerHTML = '';
  const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
  waits.forEach((w) => {
    const what = w.where === 'list-items' ? 'list item' : 'request';
    const d = el('div', 'waitnote'), top = el('div', 'waitrow'), bottom = el('div', 'waitrow');
    const t = el('div', 'waittext');
    t.textContent = (w.remind ? 'Backdrop not saved yet: ' : 'Waiting for backdrop: ') + w.label;
    const x = el('sp-action-button');
    x.setAttribute('size', 's'); x.setAttribute('data-wait-stop', w.id); x.textContent = '\u2715';
    x.setAttribute('title', w.remind ? 'Dismiss the reminder' : 'Stop waiting. The ' + what + ' stays open.');
    x.addEventListener('click', () => { dropWait(w.id); if (!w.remind) note('Stopped waiting for the backdrop: ' + w.label + '. The ' + what + ' is still open.'); });
    top.appendChild(t); top.appendChild(x);
    const st = el('div', 'waitstatus');
    st.setAttribute('data-wait', w.id); st.textContent = waitStatus[w.id] || 'checking every 3 s';
    bottom.appendChild(st);
    if (!w.remind) {
      const now = el('sp-action-button');
      now.setAttribute('size', 's'); now.setAttribute('data-wait-now', w.id); now.setAttribute('title', 'Complete the ' + what + ' now, without a backdrop'); now.textContent = 'Complete now';
      now.addEventListener('click', () => completeWait(w.id, 'completed without a backdrop', true));
      bottom.appendChild(now);
    }
    d.appendChild(top); d.appendChild(bottom);
    waitsEl.appendChild(d);
  });
}
// Complete one waiting request. One that is no longer open (completed or released elsewhere) just clears.
// manual = Complete now: it always answers, and a failure always shows. The timed check shows a failure once.
async function completeWait(id, why, manual) {
  const w = waits.find((o) => o.id === id);
  if (!w) return;
  if (w.remind) { dropWait(id); note('Backdrop saved: ' + w.label + '.'); return; }
  if (waitBusy.has(id)) { if (manual) note('Already completing ' + w.label + '.'); return; }
  waitBusy.add(id);
  setWaitStatus(id, (manual ? 'completing' : 'backdrop saved \u00b7 completing') + '\u2026');
  try {
    const c = await within(R.api('GET', COMMUNITY + 'claim?kind=' + w.kind + '&name=' + encodeURIComponent(w.name)), 'the request lookup');
    if (c.status === 'none' || c.status === 'done' || (c.status === 'match' && c.id !== id)) { dropWait(id); note('Backdrop wait cleared: ' + w.label + ' is no longer open.'); return; }
    const skip = claimSkip(c, w);
    if (skip) throw new Error(skip);
    await within(R.api('POST', COMMUNITY + w.where + '/' + id + '/complete'), 'the complete');
    dropWait(id);
    note('Completed ' + w.label + ' (' + why + ').');
  } catch (e) {
    const msg = String(e && e.message ? e.message : e).replace(/\.$/, '');
    setWaitStatus(id, clock() + ' \u00b7 complete failed: ' + msg + ' \u00b7 retrying');
    if (manual || !w.failed) { w.failed = true; fail('Could not complete ' + w.label + ': ' + msg + '. Still waiting. It retries.'); }
  } finally { waitBusy.delete(id); }
}
// Waits past WAIT_DAYS go. A request's wait says so in red, because the request is still open.
function expireWaits() {
  const limit = Date.now() - WAIT_DAYS * 86400000;
  waits.filter((w) => !(w.at > limit) && !waitBusy.has(w.id)).forEach((w) => {
    dropWait(w.id);
    if (w.remind) note('Backdrop reminder dropped after ' + WAIT_DAYS + ' days: ' + w.label + '.');
    else fail('Stopped waiting for the backdrop after ' + WAIT_DAYS + ' days: ' + w.label + '. The ' + (w.where === 'list-items' ? 'list item' : 'request') + ' is still open.');
  });
}
let waitChecking = false;
async function checkWaits() {
  if (!waits.length || waitChecking) return;
  waitChecking = true;
  try {
    expireWaits();
    const linked = R.isConfigured(R.getConfig());
    for (const w of waits.slice()) {
      if (waitBusy.has(w.id)) continue;
      if (!linked) { setWaitStatus(w.id, 'no Posterflow link in \ud83d\udd17'); continue; }
      let saved;
      try { saved = await within(backgroundSaved(w.file), 'the check'); }
      catch (e) { setWaitStatus(w.id, clock() + ' \u00b7 ' + (e && e.message ? e.message : e) + ' \u00b7 retrying'); continue; }
      if (saved) await completeWait(w.id, 'backdrop saved', false);
      else if (!/failed/.test(waitStatus[w.id] || '')) setWaitStatus(w.id, 'checked ' + clock() + ' \u00b7 not saved yet');
    }
  } finally { waitChecking = false; }
}
renderWaits();
setInterval(checkWaits, WAIT_CHECK_MS);

// ---- Upload / Complete buttons: the active PSD's claimed request, two presses each ----
// The first press asks Posterflow and says what would happen; the same press again within HOLD_MS does
// it. Upload sends what Posterflow picked from its own export folders (the newest export of each
// poster the request asks for), as its own Upload button is set. The panel sends no files.
const uploadHold = makeHold(ask), completeHold = makeHold(ask);
let cmBusy = false;
const posters = (n) => n + ' poster' + (n === 1 ? '' : 's');
// Run one press of a community button: `second(kept)` when it repeats a held press, else `first(name, key)`.
async function communityPress(label, btn, hold, first, second) {
  if (cmBusy || finishBusy || batchBusy || sqBusy || sqArmed || busy) { note(label + ': busy. Wait for the current step.'); return; }
  if (!hasDoc()) { note(label + ': no document open.'); return; }
  const key = String(app.activeDocument.id), name = exportBase(), kept = hold.again(key);
  cmBusy = true;
  btn.textContent = '…';
  try {
    if (kept) { note(label + ': ' + await second(kept) + '.'); flash(btn, '✓', label); return; }
    const said = await first(name, key);
    if (said) note(label + ': ' + said + '.');
    btn.textContent = label;
  } catch (e) { flash(btn, '✗', label); fail(label + ': ' + String(e && e.message ? e.message : e).replace(/\.$/, '') + '.'); }
  finally { cmBusy = false; }
}
const onUpload = () => communityPress('Upload', uploadBtn, uploadHold, async (name, key) => {
  const c = await R.api('GET', COMMUNITY + 'upload?name=' + encodeURIComponent(name));
  const skip = claimSkip(c, { name });
  if (skip) return skip;
  if (c.upload.problem) return c.upload.problem;
  if (!c.folders) return 'no export folder is set in Posterflow';
  if (!c.files.length) return 'no export for ' + c.label + ' in the Posterflow export folders';
  const list = c.files.map((f) => f.poster + ' (' + f.age + ')').join(', ');
  uploadHold.set(key, 'Upload: ' + posters(c.files.length) + ' for ' + c.label + ': ' + list + '. Press Upload again to ' + c.upload.will + '.' + (c.upload.aside ? ' ' + c.upload.aside + '.' : ''),
    { at: COMMUNITY + c.where + '/' + c.id });
  return '';
}, async (kept) => (await R.api('POST', kept.at + '/upload')).did);
const onComplete = () => communityPress('Complete', completeBtn, completeHold, async (name, key) => {
  const c = await R.api('GET', COMMUNITY + 'claim?name=' + encodeURIComponent(name));
  const skip = claimSkip(c, { name });
  if (skip) return skip;
  completeHold.set(key, 'Complete: ' + c.label + '. Press Complete again to mark it complete.', { at: COMMUNITY + c.where + '/' + c.id, label: c.label });
  return '';
}, async (kept) => {
  await R.api('POST', kept.at + '/complete');
  return kept.label + ' is marked complete';
});

async function finishGuard(label) {
  if (cmBusy || editBusy || finishBusy || batchBusy || sqBusy || sqArmed || busy) { note(label + ': busy (close Square Art or wait for the current step).'); return false; }
  if (!hasDoc()) { note(label + ': no document open.'); return false; }
  await refresh();
  if (!rootPosterGroup()) { note(label + ': not a poster PSD (no POSTER group at the top level). Nothing was changed.'); return false; }
  return true;
}
// The end of every finish: the community steps start, then Square Art. Square Art never opens on another document.
async function finishSquareArt(F, f) {
  finishCommunity(f);   // in the background: its own note says how it went
  if (!steps[f].squareArt) { F.report('done'); return; }
  if (F.switched('Square Art')) { F.report(); return; }
  F.report('opening Square Art');
  await onSquareArt();
}

async function onFinishMovie() {
  const label = 'MOVIE', S_ = steps.movie;
  if (!(await finishGuard(label))) return;
  finishBusy = true;
  startFinishX('movie');
  const F = finishRun(label, app.activeDocument.id);
  try {
    if (S_.clear) await F.step('cleared', stepClear);
    if (S_.main) await F.step('main', stepMain);
    if (S_.logoName) await F.step('logo name', stepLogoName);
    if (S_.trim) await F.step('trim', stepTrim);
    if (S_.save) {
      if (!(await F.step('saved', saveCore))) { flash(saveBtn, '✗', '💾'); F.report('STOPPED, PSD not saved'); return; }
      flash(saveBtn, '✓', '💾');
    }
    if (F.failed.length) { F.report((S_.save ? 'PSD saved; ' : '') + skippedTail('movie', ['coverage', 'jpg', 'logoPng', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.coverage && !(await F.step('coverage', () => stepCoverage(visibleTarget())))) { F.report(skippedTail('movie', ['jpg', 'logoPng', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.jpg && !(await F.step('JPG', stepJpg))) { F.report(skippedTail('movie', ['logoPng', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.logoPng && !(await F.step('logo PNG', stepLogoPng))) { F.report(skippedTail('movie', ['upload', 'backdrop', 'complete', 'squareArt'])); return; }
    await finishSquareArt(F, 'movie');
  } catch (e) { showError(e); }
  finally { finishBusy = false; }
}

// ---- Right-click COLLECTION: the MOVIE finish with "c" in place of "main" ----
const stepCollectionTag = () => renameVisibleIn(rootPosterGroup(), 'POSTER', 'c', true);
// The JPG goes through the tag batch filtered to "c": only the batch places the logo on the collection
// ruler and names the file " - Collection" (plain for a "… Collection" PSD). The JPG button does neither.
const stepCollectionJpg = async () => {
  await startBatch('tags');
  if (!batchConvention || !batchItems.some((it) => it.key === 'c')) { closeBatch(); throw new Error('no POSTER layer tagged "c"'); }
  batchWarnings = batchWarnings.filter((w) => w.indexOf('"c"') >= 0);   // only warnings about the c export matter here
  batchInput.value = 'c';
  const r = await runBatch();
  if (!r.ok) throw new Error(r.reason);
};
async function onFinishCollection() {
  const label = 'COLLECTION', S_ = steps.collection;
  if (!(await finishGuard(label))) return;
  finishBusy = true;
  startFinishX('collection');
  const F = finishRun(label, app.activeDocument.id);
  try {
    if (S_.c) await F.step('c', stepCollectionTag);
    if (S_.logoName) await F.step('logo name', stepLogoName);
    if (S_.trim) await F.step('trim', stepTrim);
    if (S_.save) {
      if (!(await F.step('saved', saveCore))) { flash(saveBtn, '✗', '💾'); F.report('STOPPED, PSD not saved'); return; }
      flash(saveBtn, '✓', '💾');
    }
    if (F.failed.length) { F.report((S_.save ? 'PSD saved; ' : '') + skippedTail('collection', ['coverage', 'jpg', 'logoPng', 'saveAgain', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.coverage && !(await F.step('coverage', async () => stepCoverage(await taggedTargets((v) => v.nm === 'c'))))) { F.report(skippedTail('collection', ['jpg', 'logoPng', 'saveAgain', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.jpg && !(await F.step('JPG', stepCollectionJpg))) { F.report(skippedTail('collection', ['logoPng', 'saveAgain', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.logoPng && !(await F.step('logo PNG', stepLogoPng))) { F.report(skippedTail('collection', ['saveAgain', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
    if (S_.saveAgain) {   // the export toggled layers after the first save; this one spares a prompt on close
      const saved = await F.step('saved again', saveCore);
      flash(saveBtn, saved ? '✓' : '✗', '💾');
    }
    await finishSquareArt(F, 'collection');
  } catch (e) { showError(e); }
  finally { finishBusy = false; }
}

async function finishShowTail(F) {
  const S_ = steps.show;
  if (S_.logoPng && !(await F.step('logo PNG', stepLogoPng))) { F.report(skippedTail('show', ['showOnly', 'saveAgain', 'upload', 'backdrop', 'complete', 'squareArt'])); return; }
  const shown = S_.showOnly ? await F.step('show only', stepShowOnly) : true;
  if (S_.saveAgain) {   // the first save holds the work; this one spares a prompt on close
    const saved = await F.step('saved again', saveCore);
    flash(saveBtn, saved ? '✓' : '✗', '💾');
  }
  if (!shown) { F.report(skippedTail('show', ['upload', 'backdrop', 'complete', 'squareArt'])); return; }
  await finishSquareArt(F, 'show');
}

// The SHOW finish's prep steps, then the save. false = stopped here and reported.
const SHOW_TAIL = ['logoPng', 'showOnly', 'saveAgain', 'upload', 'backdrop', 'complete', 'squareArt'];
async function showPrep(F) {
  const S_ = steps.show;
  if (S_.clear) await F.step('cleared', stepClear);
  if (S_.logoName) await F.step('logo name', stepLogoName);
  if (S_.trim) await F.step('trim', stepTrim);
  if (S_.save) {
    if (!(await F.step('saved', saveCore))) { flash(saveBtn, '✗', '💾'); F.report('STOPPED, PSD not saved'); return false; }
    flash(saveBtn, '✓', '💾');
  }
  if (F.failed.length) { F.report((S_.save ? 'PSD saved; ' : '') + skippedTail('show', ['coverage', 'batch'].concat(SHOW_TAIL))); return false; }
  return true;
}

// Leave a SHOW finish waiting for Run in the range box; Run (runBatchFromUI) resumes it with the batch's result.
function waitForRun(F, docId, stepName) {
  finishWait = {
    docId,
    resume: async (r) => {
      try {
        const ran = await F.step(stepName, async () => { if (!r.ok) throw new Error(r.reason); return { info: jpgs(r.count) }; });
        if (ran) await finishShowTail(F); else F.report(skippedTail('show', SHOW_TAIL));
      } catch (e) { showError(e); }
      finally { finishBusy = false; }
    },
  };
}
// The show item when POSTER holds exactly one layer and it is tagged show / main; else null.
async function soleShowItem() {
  await refresh();
  const base = baseName();
  const scan = M.scanBatch(lastTree || app.activeDocument, constants, B.normName(base));
  if (!scan.poster || scan.poster.count !== 1) return null;
  const items = B.buildTagItems(scan.variants, scanTexts(scan), base).items;
  return items.length === 1 && items[0].key === 'show' ? items[0] : null;
}

async function onFinishShow(mode) {   // 'tags' = orange ⚡ (unattended), 'seasons' = blue ⚡ (waits for Run)
  const label = 'SHOW (' + (mode === 'seasons' ? 'blue' : 'orange') + ' \u26a1)', S_ = steps.show;
  if (!(await finishGuard(label))) return;
  finishBusy = true;
  startFinishX('show');
  const docId = app.activeDocument.id;
  const F = finishRun(label, docId);
  let waiting = false;
  try {
    if (!(await showPrep(F))) return;
    if (!S_.batch) { await finishShowTail(F); return; }
    if (F.switched('batch')) { F.report(skippedTail('show', SHOW_TAIL)); return; }
    if (mode === 'tags') {
      let probs = [];
      if (S_.tagCheck) {
        const base = baseName();
        await refresh();   // the prep steps renamed layers; check the tags as they are now
        probs = B.tagProblems(M.scanBatch(lastTree || app.activeDocument, constants, B.normName(base)), base);
      }
      // Checked before the tag stop, so one report lists the blank canvas and the tag problems together.
      const covered = !S_.coverage || await F.step('coverage', async () => stepCoverage(await taggedTargets()));
      if (probs.length) { F.report('STOPPED. Fix these tags, then right-click SHOW again:  ' + probs.join('   \u00b7   ')); return; }
      if (!covered) { F.report(skippedTail('show', ['batch'].concat(SHOW_TAIL))); return; }
      // One poster (POSTER holds a single show / main layer): wait for the seasons it should also make.
      if (await soleShowItem()) {
        await startBatch('single');
        waitForRun(F, docId, 'tag batch');
        waiting = true;
        F.report((S_.save ? 'PSD saved. ' : '') + 'One poster: press Run for the main JPG and Season 1, or type more seasons');
        return;
      }
      if (!(await F.step('tag batch', stepTagBatch))) { F.report(skippedTail('show', SHOW_TAIL)); return; }
      await finishShowTail(F);
    } else {
      if (S_.coverage && !(await F.step('coverage', () => stepCoverage(visibleTarget())))) { F.report(skippedTail('show', ['batch'].concat(SHOW_TAIL))); return; }
      await startBatch('seasons');
      if (batchRow.classList.contains('hidden')) { F.report('no season layers to batch; ' + skippedTail('show', SHOW_TAIL)); return; }
      waitForRun(F, docId, 'season batch');
      waiting = true;
      F.report((S_.save ? 'PSD saved. ' : '') + 'Type the seasons and press Run');
    }
  } catch (e) { showError(e); }
  finally { if (!waiting) finishBusy = false; }
}

// ---- Right-click LIMITED SERIES: the main JPG and " - Season 1" with the limited series text ----
// A show layer and a cls layer export as tagged, like the orange SHOW finish. One poster (tagged
// show / main / cls, or untagged) makes both JPGs. A season layer stops it: a limited series has none.
// What to export: { stop: why } or { items, targets, probs } (items null = the tag batch as it stands).
async function limitedSeriesPlan(tagCheck) {
  await refresh();   // the prep steps renamed layers; read the tags as they are now
  const base = baseName(), baseNorm = B.normName(base), src = lastTree || app.activeDocument;
  const scan = M.scanBatch(src, constants, baseNorm);
  const keysOf = (v) => B.parseTagName(v.nm, baseNorm).tags.map((t) => t.key);
  const seasonal = scan.variants.filter((v) => keysOf(v).some((k) => /^s\d/.test(k)));
  if (seasonal.length) return { stop: 'a limited series has no season posters, but POSTER has ' + seasonal.map((v) => '"' + v.nm + '"').join(', ') + '. Rename or delete them, then right-click LIMITED SERIES again' };
  const has = (k) => scan.variants.some((v) => keysOf(v).indexOf(k) >= 0);
  if (has('show') && has('cls')) return { items: null, targets: await taggedTargets(), probs: tagCheck ? B.tagProblems(scan, base) : [] };
  // One poster: the layer tagged show / cls, else the topmost visible POSTER layer.
  let pgi = -1;
  for (let i = 0; i < src.layers.length && pgi < 0; i++) { const L = src.layers[i]; if (L.kind === constants.LayerKind.GROUP && /^\s*poster\s*$/i.test(L.name)) pgi = i; }
  const kids = pgi < 0 ? [] : Array.from(src.layers[pgi].layers || []);
  const one = scan.variants.find((v) => keysOf(v).some((k) => k === 'show' || k === 'cls'));
  const idx = one ? one.p[one.p.length - 1] : kids.findIndex((K) => K.visible);
  if (idx < 0) return { stop: 'no visible poster in POSTER; nothing exported' };
  const at = [pgi, idx];
  const others = kids.map((_, i) => ({ nm: '', p: [pgi, i], v: false })).filter((o) => o.p[1] !== idx);   // hidden for both JPGs
  const items = B.buildTagItems([{ nm: 'show', p: at, v: true }, { nm: 'cls', p: at, v: true }].concat(others), scanTexts(scan), base).items;
  const node = layerByPath[T.key(at)], live = Array.from(rootPosterGroup().layers || []);
  return { items, targets: [{ label: kids[idx].name, layer: (node && live[live.map((K) => K.id).indexOf(node.id)]) || null }], probs: [] };
}
// Run a finish's own export items through the tag batch (same bar, same writes).
const stepItemsBatch = async (items) => {
  batchMode = 'tags'; batchConvention = true; batchItems = items; batchWarnings = []; batchInput.value = '';
  batchBar.classList.remove('hidden'); batchHelp.classList.add('hidden');
  const r = await runBatch();
  if (!r.ok) throw new Error(r.reason);
  return { info: jpgs(r.count) };
};
async function onFinishCls() {
  const label = 'LIMITED SERIES', S_ = steps.show;
  if (!(await finishGuard(label))) return;
  finishBusy = true;
  startFinishX('show');
  const F = finishRun(label, app.activeDocument.id);
  try {
    if (!(await showPrep(F))) return;
    if (!S_.batch) { await finishShowTail(F); return; }
    if (F.switched('batch')) { F.report(skippedTail('show', SHOW_TAIL)); return; }
    const plan = await limitedSeriesPlan(S_.tagCheck);
    if (plan.stop) { F.report('STOPPED: ' + plan.stop); return; }
    const covered = !S_.coverage || await F.step('coverage', () => stepCoverage(plan.targets));
    if (plan.probs.length) { F.report('STOPPED. Fix these tags, then right-click LIMITED SERIES again:  ' + plan.probs.join('   \u00b7   ')); return; }
    if (!covered) { F.report(skippedTail('show', ['batch'].concat(SHOW_TAIL))); return; }
    if (!(await F.step('tag batch', () => (plan.items ? stepItemsBatch(plan.items) : stepTagBatch())))) { F.report(skippedTail('show', SHOW_TAIL)); return; }
    await finishShowTail(F);
  } catch (e) { showError(e); }
  finally { finishBusy = false; }
}

function abandonFinish(why) {
  if (!finishWait) return;
  finishWait = null; finishBusy = false;
  note('SHOW: ' + why + '; ' + skippedTail('show', SHOW_TAIL) + '.');
}
// Run / Enter in the range box: run the batch, then resume a waiting blue finish. Run only counts on
// the PSD that finish is for; from another document nothing runs.
// What a manual ⚡ Run is about to export: the visible poster (blue), or the tagged layers its filter keeps (orange).
function batchTargets() {
  if (!batchConvention) return visibleTarget();
  const fk = B.parseTagFilter(batchInput.value), baseNorm = B.normName(baseName());
  return taggedTargets((v) => !fk || B.parseTagName(v.nm, baseNorm).tags.some((t) => fk[t.key]));
}
let batchChecking = false;
async function runBatchFromUI() {
  if (finishWait && (!hasDoc() || app.activeDocument.id !== finishWait.docId)) {
    batchNote('This finish is waiting on another PSD. Switch back to it and press Run, or press \u2715 to cancel.');
    return;
  }
  if (batchChecking) return;
  if (!batchConvention && !batchBusy) { const why = seasonRangeProblem(); if (why) { batchNote(why); return; } }   // before the blank canvas check
  if (!finishWait && !batchBusy && hasDoc()) {   // a finish waiting for Run has made its own check
    const key = app.activeDocument.id + '|' + batchMode + '|' + String(batchInput.value || '').trim();
    if (!batchHold.again(key)) {
      batchChecking = true;
      let blank = '';
      try { blank = await blankCanvas(batchTargets); } finally { batchChecking = false; }
      if (blank) {
        batchHold.set(key, 'Batch not started. ' + blank + '. Press Run again to export anyway.');
        batchNote('Blank canvas found. Press Run again to export anyway.');
        return;
      }
    }
  }
  const r = await runBatch();
  if (!finishWait || r.reason === 'nothing' || r.reason === 'busy') return;   // bar still open — fix the input, press Run again
  const w = finishWait; finishWait = null;
  await w.resume(r);
}

// ---- Batch export ----
const batchNote = (t) => { batchMsgEl.textContent = t; };
const modeBtn = () => batchMode === 'seasons' ? seasonsBtn : batchBtn;
const resetBolts = () => { batchBtn.textContent = '⚡'; seasonsBtn.textContent = '⚡'; };
const closeBatch = () => { if (!batchBusy) batchBar.classList.add('hidden'); };

// mode: 'tags' (orange), 'seasons' (blue), 'single' (an orange finish with one poster: its main JPG, then seasons).
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
      batchConvention = false; batchItems = [];   // never leave a stale item list for a finish to find
      return;
    }
    batchConvention = true;
    batchItems = built.items;
    batchWarnings = built.warnings;   // shown when the run actually starts, not at this preview
    batchRow.classList.remove('hidden'); batchInput.classList.remove('hidden');
    batchInput.value = ''; batchInput.setAttribute('placeholder', 'blank = all · filter: c, cls, main, s1-8');
    batchNote('Found ' + batchItems.length + ' tagged export(s)' + (built.warnings.length ? ' (' + built.warnings.length + ' warning(s))' : '') + '. Run all, or type a filter.');
  } else if (batchMode === 'single') {
    batchWarnings = []; batchConvention = false;
    batchRow.classList.remove('hidden'); batchInput.classList.remove('hidden');
    batchInput.value = ''; batchInput.setAttribute('placeholder', 'blank = main + Season 1 \u00b7 e.g. 3, 0-5'); batchInput.focus();
    batchNote('One poster. Run exports the main JPG and Season 1 from it. For more seasons type e.g. "3" (=1–3) or "0-5" (0 = Specials).');
  } else {
    batchWarnings = [];
    if (!(T.byRole(model, 'season').length || model.singles.length)) { batchNote('No SEASONS group or season layers found. For layers tagged s0/s1…/main/show, use the amber ⚡ instead.'); return; }
    batchConvention = false;
    batchRow.classList.remove('hidden'); batchInput.classList.remove('hidden');
    batchInput.value = ''; batchInput.setAttribute('placeholder', 'e.g. 8, 3-6, 0-5'); batchInput.focus();
    batchNote('Which seasons? e.g. "8" (=1–8), "3-6", "0-5" (0 = Specials), "2015-2020" (years).');
  }
}

// The blue ⚡ needs a typed range: a blank box never means every season the template has text for
// (60 and Specials). What to tell the user, or '' when the box holds a range. A one-poster finish
// accepts blank: its main JPG and Season 1.
function seasonRangeProblem() {
  const typed = String(batchInput.value || '').trim();
  if (!typed) return batchMode === 'single' ? '' : 'Type the seasons first: e.g. "8" (=1–8), "3-6", "0-5" (0 = Specials) or "2015-2020" (years).';
  return B.parseRange(typed).start == null ? 'Could not read "' + typed + '". Type seasons like "8" (=1–8), "3-6" or "0-5" (0 = Specials).' : '';
}

async function runBatch() {   // → { ok, count } or { ok: false, reason: 'busy' | 'nothing' | 'no folder chosen' | <error> }
  if (batchBusy) return { ok: false, reason: 'busy' };
  if (!hasDoc()) { batchNote('No document open.'); return { ok: false, reason: 'nothing' }; }
  if (!batchConvention) {
    const why = seasonRangeProblem();
    if (why) { batchNote(why); return { ok: false, reason: 'nothing' }; }
    // One poster: its main JPG leads the seasons, read again now in case the layers changed while the box waited.
    let lead = [];
    if (batchMode === 'single') {
      const one = await soleShowItem();
      if (!one) { batchNote('POSTER no longer holds a single show poster. Press \u2715, then right-click SHOW again.'); return { ok: false, reason: 'nothing' }; }
      lead = [one];
    }
    const typed = String(batchInput.value || '').trim(), r = typed ? B.parseRange(typed) : { start: 1, end: 1 };   // blank only gets this far for one poster
    const seasons = (B.isYearRange(r) && T.byRole(model, 'year').length) ? B.yearItems(model, r.start, r.end) : B.seasonItems(model, r.start, r.end);
    batchItems = lead.concat(seasons);
  } else {
    const fk = B.parseTagFilter(batchInput.value);
    if (fk) batchItems = batchItems.filter((it) => fk[it.key]);
  }
  if (!batchItems.length) { batchNote('Nothing matched, nothing to export.'); return { ok: false, reason: 'nothing' }; }
  batchBusy = true; modeBtn().textContent = '…'; batchRow.classList.add('hidden');
  batchWarnings.forEach((w) => note('Batch: ' + w));
  const style = curStyle, batchCtx = remoteCtx(), base = (batchCtx && batchCtx.name) || baseName();
  let ok = 0, result = { ok: false, reason: 'error', count: 0 };
  // The batch only ever edits and exports the PSD it started on.
  const docId = app.activeDocument.id;
  const sameDoc = () => hasDoc() && app.activeDocument.id === docId;
  const stopIfSwitched = () => { if (!sameDoc()) throw new Error('another document became active; the batch stopped after ' + jpgs(ok)); };
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
      stopIfSwitched();
      batchNote('Exporting ' + (i + 1) + '/' + batchItems.length + '…');
      const changes = batchItems[i].changes;
      await runExclusive(() => writeVisible(pairsFor(changes), 'Batch variant ' + (i + 1)));
      // Collection export: temporary logo copy on the collection ruler (removed right after).
      // Placement/cleanup are best-effort — any failure warns and the export proceeds bare.
      let placed = null;
      if (batchItems[i].collection) {
        stopIfSwitched();
        try { placed = await runExclusive(() => TL.placeCollectionLogo(app.activeDocument, constants)); }
        catch (e) { placed = null; fail('Batch: collection logo placement failed (' + (e && e.message ? e.message : e) + '); exported without repositioning the logo.'); }
        if (placed && placed.warning) note('Batch: ' + placed.warning);
        if (placed && placed.note) note('Batch: ' + placed.note);
      }
      let res;
      try {
        stopIfSwitched();
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
          // The cleanup deletes by layer id in the active document, so it never runs on another one.
          if (sameDoc()) { try { await runExclusive(() => TL.removeCollectionLogo(app.activeDocument, constants, placed)); } catch (_) {} }
          else fail('Batch: the temporary collection logo is still in the PSD the batch started on. There, delete the "' + TL.TMP_LOGO + '" layer and show the LOGO group.');
        }
      }
      if (res.ok) { ok++; keepFinishJpg(res); }
      else if (res.reason === 'cancelled') { batchNote('Stopped: no folder chosen.'); result.reason = 'no folder chosen'; return result; }
    }
    batchNote('Done: exported ' + ok + ' JPG' + (ok === 1 ? '' : 's') + '.');
    result = { ok: true, count: ok };
  } catch (e) {
    batchNote('Stopped: ' + (e && e.message ? e.message : e));
    result.reason = String(e && e.message ? e.message : e);
    if (!finishBusy) fail('Batch stopped: ' + result.reason);   // the bar closes in a moment; a finish reports its own
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

// ---- Edit in tab / Send back ----
// Edit opens one layer in a tab of its own: a logo selected in the Layers panel, else the poster on
// screen. Send back, pressed in that tab, puts the cleaned pixels back (TL.sendBackFromTab). In an edit
// tab the row reads Save BG (a poster tab only), Send back and X. editTabs: the tab's document id ->
// { srcDocId, layerId, layer, kind, tag, logoName, left, top, w, h, srcName, bgName }. In memory only,
// like remoteDocs.
const editTabs = {};
let editBusy = false;
const editCancelHold = makeHold(ask), saveBgHold = makeHold(ask);
function updateEditUI() {
  const open = Array.from(app.documents || []).map((d) => d.id);
  Object.keys(editTabs).forEach((id) => { if (open.indexOf(Number(id)) < 0) delete editTabs[id]; });   // a tab closed by hand
  const tabCtx = hasDoc() ? editTabs[app.activeDocument.id] : null, inTab = !!tabCtx;
  editBtn.classList.toggle('hidden', inTab || sqArmed);   // Crop and X own the row while Square Art works
  squareArtBtn.classList.toggle('hidden', inTab);
  posterExpBtn.classList.toggle('hidden', inTab || sqArmed);
  saveBgBtn.classList.toggle('hidden', !inTab || tabCtx.kind !== 'poster');
  sendBackBtn.classList.toggle('hidden', !inTab);
  editCancelBtn.classList.toggle('hidden', !inTab);
}
// The layer Edit opens, and whether it is a logo: { layer, logo } or null.
async function editTarget(doc) {
  const pg = rootPosterGroup(), lg = await TL.findLogoGroup(doc, constants);
  const base = B.normName(baseName(doc));
  const tagged = (L) => { const t = B.parseTagName(String(L.name).trim().toLowerCase(), base); return t.tags.length > 0 || !!t.reject; };
  const wide = (L) => (L.bounds.right - L.bounds.left) >= doc.width * 0.9;   // poster art spans the canvas; a logo does not
  const selected = Array.from(doc.activeLayers || []);
  const sel = selected[0], parentId = sel && sel.parent ? sel.parent.id : 0;
  // A selected logo wins: a layer in LOGO, or an untagged POSTER layer narrower than a poster.
  if (sel && ((lg && parentId === lg.id) || (pg && parentId === pg.id && !tagged(sel) && !wide(sel)))) return { layer: sel, logo: true };
  if (!pg) return null;
  // Else the poster on screen: a visible POSTER layer that is tagged, or untagged and full width (a pasted
  // backdrop). With several visible, the selected one, else the top one. None visible: a selected tagged layer.
  const kids = Array.from(pg.layers || []), isSel = (L) => selected.some((X) => X.id === L.id);
  const shown = kids.filter((L) => L.visible && (tagged(L) || wide(L)));
  const layer = shown.find(isSel) || shown[0] || kids.filter(tagged).find(isSel);
  return layer ? { layer, logo: false } : null;
}
async function onEdit() {
  if (editBusy || cmBusy || finishBusy || batchBusy || sqBusy || sqArmed || busy) { note('Edit: busy. Wait for the current step or close Square Art.'); return; }
  if (!hasDoc()) { note('No document open.'); return; }
  const doc = app.activeDocument;
  if (editTabs[doc.id]) { note('This is already an edit tab. Press Send back when it is clean.'); return; }
  editBusy = true;   // before the first wait, so a second press cannot open the layer twice
  let L = null;
  try {
    const target = await editTarget(doc);
    if (!target) { note('Edit: no visible poster in POSTER to edit, and no logo selected.'); return; }
    L = target.layer;
    const b = L.bounds;
    const ctx = { srcDocId: doc.id, layerId: L.id, layer: L, kind: target.logo ? 'logo' : 'poster', tag: L.name, logoName: logoLayerName(),
      left: b.left, top: b.top, w: b.right - b.left, h: b.bottom - b.top, srcName: cleanTitle(baseName(doc)), bgName: backgroundName(exportBase()) };
    const tab = await runExclusive(() => TL.openLayerInTab(doc, L, L.name + ' · ' + ctx.srcName));
    editTabs[tab.id] = ctx;
    note('Editing ' + (target.logo ? 'the logo ' : '') + '"' + L.name + '" in a new tab. Clean it up, then press Send back.');
  } catch (e) { fail('Edit' + (L ? ' "' + L.name + '"' : '') + ': ' + (e && e.message ? e.message : e)); }
  finally { editBusy = false; updateEditUI(); }
}
async function onSendBack() {
  if (editBusy) return;
  const ctx = hasDoc() ? editTabs[app.activeDocument.id] : null;
  if (!ctx) { note('Send back: switch to an edit tab first. Open one with Edit.'); return; }
  const tab = app.activeDocument, tabId = tab.id, kept = '. The tab stays open, so nothing is lost.';
  editBusy = true;
  try {
    const r = await runExclusive(() => TL.sendBackFromTab(tab, ctx, constants));
    if (!r.ok) {
      fail('Send back "' + ctx.tag + '": ' + (r.reason === 'psd-closed' ? 'the PSD "' + ctx.srcName + '" is closed'
        : r.reason === 'no-logo-group' ? 'this looks like a logo (it is see-through), but the PSD has no LOGO group'
        : 'the layer "' + ctx.tag + '" is no longer in the PSD') + kept);
      return;
    }
    delete editTabs[tabId];
    if (r.mode === 'newLogo') {
      note('Sent back as a logo: "' + ctx.logoName + '" in LOGO, placed like Place Logo (' + Math.round(r.share * 100) + '% see-through).'
        + (r.hidOld ? ' The earlier logo was hidden.' : '') + ' The poster is unchanged.');
    } else {
      const what = (r.mode === 'logo' ? 'the logo ' : '') + '"' + ctx.tag + '"';
      note(r.refit ? 'Sent ' + what + ' back. Its size changed, so it was ' + (r.mode === 'logo' ? 'placed like Place Logo.' : 'fitted like Fit Poster.')
        : 'Sent ' + what + ' back to the same spot.');
    }
    refresh();
  } catch (e) { fail('Send back "' + ctx.tag + '": ' + (e && e.message ? e.message : e) + kept); }
  finally { editBusy = false; updateEditUI(); }
}
// Save BG: this poster tab, at its full size, becomes the title's background ("<PSD name> -
// background.jpg", the file the gear's backdrop step waits for). With the Posterflow link it goes to
// Posterflow's background folder, else to the remembered backgrounds folder. Then the tab closes and the
// backdrop layer leaves the PSD (one undo step). A background already saved is replaced on a second press.
async function onSaveBg() {
  if (editBusy) return;
  const ctx = hasDoc() ? editTabs[app.activeDocument.id] : null;
  if (!ctx || ctx.kind !== 'poster') { note('Save BG: switch to a backdrop edit tab first. Open one with Edit.'); return; }
  const tab = app.activeDocument, tabId = tab.id, key = String(tabId), file = ctx.bgName, kept = '. The tab stays open, so nothing is lost.';
  if (!Array.from(app.documents || []).some((d) => d.id === ctx.srcDocId)) { fail('Save BG: the PSD "' + ctx.srcName + '" is closed' + kept); return; }
  editBusy = true;
  try {
    let folder = null, viaLink = R.isConfigured(R.getConfig()), there = false;
    if (viaLink) {
      try { there = await backgroundSaved(file); }
      catch (e) { if (!/No background export folder/.test(String(e && e.message))) throw e; viaLink = false; }   // Posterflow has no background folder set
    }
    if (!viaLink) {
      folder = await FS.getBackgroundFolder({});
      if (!folder) { note('Save BG cancelled: no backgrounds folder chosen.'); return; }
      try { await folder.getEntry(file); there = true; } catch (_) { there = false; }
    }
    const where = viaLink ? 'Posterflow' : folder.name;
    const replacing = !!saveBgHold.again(key);
    if (there && !replacing) { saveBgHold.set(key, 'A background for "' + ctx.srcName + '" is already in ' + where + '. Press Save BG again to replace it.'); return; }
    const size = Math.round(tab.width) + ' x ' + Math.round(tab.height);
    const render = (tmp) => core.executeAsModal(async () => { await tab.saveAs.jpg(tmp, { quality: S.JPG_QUALITY }, true); }, { commandName: 'Save as background' });
    if (viaLink) await runExclusive(async () => R.putBytes('/api/maker-tools/background-exports/' + encodeURIComponent(file), await S.renderBytes(file, render)));
    else await runExclusive(() => S.writeViaTemp(folder, file, render));
    delete editTabs[tabId];
    const removed = await runExclusive(() => TL.closeEditTabAndRemove(tab, ctx.srcDocId, ctx.layerId));
    note((replacing ? 'Replaced' : 'Saved') + ' "' + file + '" (' + size + ') in ' + where + ' and closed the edit tab. '
      + (removed ? '"' + ctx.tag + '" was removed from the PSD. Undo brings it back.' : '"' + ctx.tag + '" was already gone from the PSD.'));
    refresh();
  } catch (e) { fail('Save BG "' + ctx.tag + '": ' + (e && e.message ? e.message : e) + (editTabs[tabId] ? kept : '')); }
  finally { editBusy = false; updateEditUI(); }
}
// Close the edit tab without sending it back: the first press asks, the same press again closes it.
async function onEditCancel() {
  if (editBusy || !hasDoc() || !editTabs[app.activeDocument.id]) return;
  const tab = app.activeDocument, key = String(tab.id);
  if (!editCancelHold.again(key)) { editCancelHold.set(key, 'Press ✕ again to close this edit tab without sending it back.'); return; }
  const ctx = editTabs[tab.id];
  delete editTabs[tab.id];
  editBusy = true;
  try {
    await runExclusive(() => TL.closeEditTab(tab, ctx.srcDocId));
    note('Closed the edit tab for "' + ctx.tag + '". The PSD is unchanged.');
  } catch (e) { showError(e); }
  finally { editBusy = false; updateEditUI(); }
}
editBtn.addEventListener('click', onEdit);
sendBackBtn.addEventListener('click', onSendBack);
editCancelBtn.addEventListener('click', onEditCancel);
saveBgBtn.addEventListener('click', onSaveBg);
// Edit in tab and Send back are also Plugins > Posterflow menu items (manifest.json), so they can be
// recorded in an Action and given an F-key. A host without command support never takes the panel down.
try {
  require('uxp').entrypoints.setup({ commands: { editInTab: { run: () => onEdit() }, sendBack: { run: () => onSendBack() } } });
} catch (e) { console.log('Posterflow: menu commands unavailable', e); }

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
uploadBtn.addEventListener('click', onUpload);
completeBtn.addEventListener('click', onComplete);
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
    : batchMode === 'single'
    ? [['blank', 'main + Season 1'], ['3', 'main + seasons 1-3'], ['3-6', 'main + that range'], ['0-5', 'main + Specials + 1-5'], ['0', 'main only']]
    : [['8', 'seasons 1-8'], ['3-6', 'that range'], ['0-5', 'includes Specials'], ['2015-2020', 'year layers']];
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
updateCommunityBar();

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
  updateCommunityBar();
  note('Posterflow server link saved.');
});
linkToggleBtn.addEventListener('click', () => {
  const cfg = R.getConfig();
  cfg.url = String(linkUrl.value || '').trim() || cfg.url;
  cfg.enabled = !cfg.enabled;
  R.setConfig(cfg);
  linkStatus = null;
  refreshLinkBar();
  updateCommunityBar();
  note(cfg.enabled ? 'Posterflow link enabled, watching the queue.' : 'Posterflow link disabled.');
});

// Poll the server queue and open claimed PSDs. Skipped while any operation is running; errors are
// shown once each as a red note that clears itself when the link works again.
let linkErr = '', linkAlert = null;   // the link error on screen and its note
const clearLinkAlert = () => { if (linkAlert && linkAlert.parentNode) linkAlert.parentNode.removeChild(linkAlert); linkAlert = null; };
setInterval(async () => {
  if (busy || batchBusy || sqBusy) return;
  if (finishBusy && !finishWait) return;   // never open a PSD mid-finish; a finish waiting for Run doesn't hold the queue
  if (!R.isConfigured(R.getConfig())) return;
  let items;
  try { items = await R.pollQueue(); }
  catch (e) {
    const msg = e && e.message ? e.message : String(e);
    linkStatus = { ok: false, msg };
    updateLinkMsg();
    if (msg !== linkErr) { linkErr = msg; clearLinkAlert(); linkAlert = fail('Posterflow link: ' + msg); }
    return;
  }
  if (linkErr) { linkErr = ''; clearLinkAlert(); }
  linkStatus = { ok: true };
  updateLinkMsg();
  for (const item of items) {
    try {
      note('Opening "' + item.filename + '" from Posterflow…');
      const opened = await runExclusive(() => R.openQueued(item));
      remoteDocs[opened.doc.id] = { filename: item.filename, style: item.style, name: item.name, entry: opened.entry };
      refresh();
    } catch (e) {
      fail('Could not open "' + item.filename + '": ' + (e && e.message ? e.message : e));
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
  updateEditUI();   // the Edit row follows the active tab
}, 2000);

refresh();   // read whatever's open when the panel mounts
