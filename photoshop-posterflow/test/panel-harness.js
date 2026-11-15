// Panel harness: runs the real main.js + index.html in jsdom against a fake Photoshop. The Photoshop-facing
// modules (tools, save, fs, remote) are stubs that record which document each call was aimed at; toggle,
// model, batch, altkey and geometry are the real files. It right-clicks chips and presses buttons like a
// user, can switch the active document at an exact step, and reads the notes box. No Photoshop needed.
// Run from the repo root:
//   NODE_PATH=frontend/node_modules node photoshop-posterflow/test/panel-harness.js
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('  FAIL: ' + m); } };
const realNow = Date.now;
let clockAhead = 0;   // ms the panel's clock runs ahead: only ever grows, so no timed window outlives its case
Date.now = () => realNow() + clockAhead;
const settle = async () => { for (let i = 0; i < 60; i++) await new Promise((r) => setImmediate(r)); };

// ---- fake Photoshop ----
let nextLayerId = 100;
const lyr = (name, visible = false) => ({ id: nextLayerId++, name, visible, kind: 'pixel' });
const grp = (name, layers, visible = true) => ({ id: nextLayerId++, name, visible, kind: 'group', layers });
function makeDoc(id, name, posterKids) {
  return {
    id, name: name + '.psd', width: 1000, height: 1500, activeLayers: [],
    layers: [
      grp('LOGO', [lyr('logo art', true)]),
      grp('POSTER', posterKids),
      grp('SEASONS', [grp('1990-1999', [lyr('Season 1'), lyr('Season 2'), lyr('Season 3')])], false),
      lyr('Specials'), lyr('Collection'), lyr('COMPLETE LIMITED SERIES'),
    ],
  };
}
let onNextLook = null;   // runs once at the next read of app.activeDocument: between steps, when no edit is in flight
const glimpses = [];     // documents the next reads of app.activeDocument see, one each, before the real one
const app = { documents: [], _active: null,
  get activeDocument() {
    if (onNextLook) { const f = onNextLook; onNextLook = null; f(); }
    return glimpses.length ? glimpses.shift() : this._active;
  },
  set activeDocument(d) { this._active = d; } };
let modals = 0;
const modalHooks = [];   // { n, cb }: cb runs after the nth executeAsModal
const afterModal = (n, cb) => modalHooks.push({ n, cb });
const photoshop = {
  app, constants: { LayerKind: { GROUP: 'group' } },
  core: { executeAsModal: async (fn) => { const out = await fn({}); modals++; for (const h of modalHooks) if (h.n === modals) await h.cb(); return out; } },
  action: { addNotificationListener() {}, removeNotificationListener() {}, batchPlay: async () => [] },
};

// ---- recording stubs ----
let calls = [];            // { fn, doc } in call order
const afterHooks = {};     // fn name → [{ n, cb }]: cb runs after the nth call
const counts = {};
const outcome = {};        // fn name → what the stub returns instead of its default, or an Error it throws
const after = (fn, n, cb) => { (afterHooks[fn] = afterHooks[fn] || []).push({ n, cb }); };
const rec = (fn, impl) => async (...args) => {
  const docArg = args.find((a) => a && typeof a === 'object' && 'layers' in a && 'id' in a);
  calls.push({ fn, doc: docArg ? docArg.id : (app.activeDocument ? app.activeDocument.id : 0) });
  if (outcome[fn] instanceof Error) { counts[fn] = (counts[fn] || 0) + 1; throw outcome[fn]; }
  const out = fn in outcome ? outcome[fn] : (impl ? await impl(...args) : undefined);
  counts[fn] = (counts[fn] || 0) + 1;
  for (const h of (afterHooks[fn] || [])) if (h.n === counts[fn]) await h.cb();
  return out;
};
let nextTabId = 500;
let edits = [];             // one entry per layer opened in an edit tab
const closeTab = (tab, srcDocId) => { app.documents = app.documents.filter((d) => d !== tab); const src = app.documents.find((d) => d.id === srcDocId); if (src) app.activeDocument = src; };
let jpgLog = [];            // one entry per exported JPG
let coverageTargets = [];   // what the last coverage check was asked to look at
const walk = (layers, f) => layers.forEach((L) => { f(L); if (L.layers) walk(L.layers, f); });
const recCoverage = rec('checkCoverage', () => ({ problems: [], boundsOnly: [] }));
const TL = {
  readLayerTree: async () => { throw new Error('not in the harness'); },   // the panel falls back to walking the layers
  // applied in order like the real one (a batch hides every variant, then shows one: the last word wins)
  setLayersVisible: rec('setLayersVisible', (pairs) => { pairs.forEach((q) => walk(app.activeDocument.layers, (L) => { if (L.id === q.id) L.visible = q.v; })); }),
  trim: rec('trim'),
  placeSelected: rec('placeSelected', () => ({ ok: true })),
  exportLogoPng: rec('exportLogoPng', (doc, c, folder, filename) => ({ ok: true, filename, folderName: folder.name, entry: {} })),
  placeCollectionLogo: rec('placeCollectionLogo', () => ({ placed: true, copyId: 7 })),
  removeCollectionLogo: rec('removeCollectionLogo'),
  forceGradientVisible: rec('forceGradientVisible', () => false),
  hideManualCollectionLogos: rec('hideManualCollectionLogos'),
  makeCropDoc: rec('makeCropDoc', () => ({ ok: false, reason: 'noposter' })),   // Square Art stops right after opening
  setSquareSelection: rec('setSquareSelection'), readSelectionBounds: async () => null,
  selectTool: () => {}, cropSaveSquare: rec('cropSaveSquare'), closeCropDoc: rec('closeCropDoc'),
  exportSelectedLayersJpg: rec('exportSelectedLayersJpg'), LOGO_DENSITY: 0.3, TMP_LOGO: '__PFLTMPLOGO__',
  // Edit in tab: a new document becomes active; Send back and the close put the PSD back in front
  findLogoGroup: (doc) => doc.layers.find((L) => L.kind === 'group' && /^logos?$/i.test(L.name)) || null,
  openLayerInTab: rec('openLayerInTab', (doc, layer, name) => { const tab = { id: nextTabId++, name, width: 1200, height: 1500, layers: [], activeLayers: [] }; app.documents.push(tab); app.activeDocument = tab; edits.push({ doc: doc.id, layer: layer.name, name }); return tab; }),
  sendBackFromTab: rec('sendBackFromTab', (tab, ctx) => { closeTab(tab, ctx.srcDocId); return { ok: true, mode: ctx.kind, refit: false }; }),
  closeEditTab: rec('closeEditTab', (tab, srcDocId) => closeTab(tab, srcDocId)),
  closeEditTabAndRemove: rec('closeEditTabAndRemove', (tab, srcDocId) => { closeTab(tab, srcDocId); return true; }),
  checkCoverage: (doc, c, targets) => { coverageTargets = targets.map((t) => ({ label: t.label, id: t.layer ? t.layer.id : null })); return recCoverage(doc, c, targets); },
};
const S = {
  JPG_QUALITY: 9,
  savePsd: rec('savePsd'),
  savePsdRemote: rec('savePsdRemote'),
  exportJpg: rec('exportJpg', (doc, style, base, suffix) => {
    // what the JPG would show: which POSTER layers are on, and whether the limited series text is
    const seasons = doc.layers[2], decade = seasons.layers[0];   // a season text shows only when its groups are on too
    jpgLog.push({ name: base + suffix, poster: doc.layers[1].layers.filter((L) => L.visible).map((L) => L.name).join('+'), cls: doc.layers[5].visible,
      season: seasons.visible && decade.visible ? decade.layers.filter((L) => L.visible).map((L) => L.name).join('+') : '' });
    return { ok: true, filename: base + suffix + '.jpg', folderName: 'IMAGES', file: { name: base + suffix + '.jpg' } };
  }),
  exportJpgRemote: rec('exportJpgRemote', (doc, ctx, base, suffix) => ({ ok: true, filename: base + suffix + '.jpg', folderName: 'Posterflow' })),
  // Save BG: the tab rendered to bytes for Posterflow, or written into a local folder
  renderBytes: rec('renderBytes', () => new ArrayBuffer(8)),
  writeViaTemp: rec('writeViaTemp', (folder, filename) => { saves.push({ folder: folder.name, filename }); return {}; }),
};
const folder = (name) => async () => ({ name });
let askFolder = null;   // what the panel gave fs.js to ask for a folder by name
const FS = {
  getImageFolder: folder('IMAGES'), getLogoFolder: folder('LOGOS'), getSquareartFolder: folder('SQUARE'), getPosterFolder: folder('POSTERS'),
  getBackgroundFolder: async () => (local.picked ? { name: 'BACKGROUNDS', getEntry: async (n) => { if (local.files.indexOf(n) < 0) throw new Error('not found'); return {}; } } : null),
  clearAllFolders() {}, clearImageFolder() {}, setOnMissing() {}, setAsker(fn) { askFolder = fn; }, writeFileBytes: async () => ({}),
};
const remote = { on: false, queue: [], polls: 0, fail: '' };
const local = { picked: true, files: [] };   // the maker's own backgrounds folder: whether the picker gives one, and what is in it
let saves = [];                              // files written into a local folder
// Posterflow's community endpoints: what the claim lookup answers, and which call fails with what message.
// files = what the Upload button's pick finds in the export folders; unwanted = staged names the request
// does not ask for; idarr = the upload also adds to IDarr (the web app's upload settings).
// background = whether the title's background is saved in Posterflow; puts = files sent to Posterflow.
const server = { calls: [], claim: { status: 'none' }, fail: {}, files: [], folders: 2, unwanted: null, idarr: false, background: false, puts: [] };
const serverDid = (n, label) => 'posted ' + n + ' poster' + (n === 1 ? '' : 's') + ' to ' + label + (server.idarr ? ' and added ' + (n === 1 ? 'it' : 'them') + ' to IDarr (Posters)' : '');
const R = {
  getConfig: () => ({ url: 'http://pf', password: '', enabled: remote.on }), setConfig() {},
  isConfigured: () => remote.on, baseUrl: () => 'http://pf',
  pollQueue: async () => { if (remote.fail) throw new Error(remote.fail); remote.polls++; const q = remote.queue; remote.queue = []; return q; },
  openQueued: rec('openQueued', (item) => { const d = makeDoc(900 + remote.polls, item.name, [lyr('poster art', true)]); app.documents.push(d); app.activeDocument = d; return { doc: d, entry: {} }; }),
  api: async (method, path) => {
    if (path.indexOf('/api/maker-tools/artwork-exports/background/') === 0) {
      server.calls.push('GET background ' + decodeURIComponent(path.split('/').pop()));
      if (server.fail.background) throw new Error(server.fail.background);
      return { exists: server.background };
    }
    const what = decodeURIComponent(path.replace('/api/maker-tools/community/', '').replace(/[0-9a-f]{8}-[0-9a-f-]{27}/, '<id>'));
    server.calls.push(method + ' ' + what);
    const c = server.claim, label = c.label || '';
    if (/^claim\?/.test(what)) return c;
    if (/^upload\?/.test(what)) return Object.assign({}, c, c.status === 'match' && !c.upload.problem ? { folders: server.folders, files: server.files } : {});
    if (method === 'PUT') { if (server.fail.stage) throw new Error(server.fail.stage); return { staged: 1 }; }
    if (/\/upload$/.test(what)) {
      if (server.fail.post) throw new Error(server.fail.post);
      const n = server.files.length;
      return { posted: n, messages: 1, idarr: server.idarr ? n : 0, drive: 'Posters', warnings: [], did: serverDid(n, label) };
    }
    if (/\/posters$/.test(what)) {
      if (server.fail.post) throw new Error(server.fail.post);
      const staged = server.calls.filter((k) => /^PUT/.test(k)), skipped = staged.filter((k) => server.unwanted && server.unwanted.test(k)).map((k) => k.replace(/^.*\/posters\//, ''));
      const n = staged.length - skipped.length;
      return { posted: n, messages: n ? 1 : 0, idarr: server.idarr ? n : 0, drive: 'Posters', warnings: [], did: n ? serverDid(n, label) : '', skipped };
    }
    if (server.fail.complete) throw new Error(server.fail.complete);
    return { ok: true };
  },
  putBytes: async (path) => { if (server.fail.put) throw new Error(server.fail.put); server.puts.push(decodeURIComponent(path.replace('/api/maker-tools/', ''))); }, tempFile: async () => ({}), tempFolder: async () => ({ name: 'TEMP' }), readBytes: async () => new ArrayBuffer(0),
};

// ---- load the panel ----
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/<script[^>]*src="main\.js"[^>]*><\/script>/, '');
const { window } = new JSDOM(html, { url: 'http://localhost/' });
const { document } = window;
const intervals = [], timers = [];
const fakeSetInterval = (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; };   // polls run when a case calls them
const fakeSetTimeout = (fn, ms) => { timers.push({ fn, ms }); return setTimeout(fn, ms); };
const expireNotes = () => timers.filter((t) => t.ms === 15000).forEach((t) => t.fn());            // NOTE_TTL
const quietConsole = { log() {}, warn: console.warn, error: console.error };
const real = (n) => require(path.join(root, n + '.js'));
let menu = null;   // what the panel registered as Plugins > Posterflow menu commands
const mods = { photoshop, uxp: { entrypoints: { setup: (o) => { menu = o; } } }, './toggle': real('toggle'), './model': real('model'), './batch': real('batch'), './geometry': real('geometry'),
               './altkey': real('altkey'), './tools': TL, './save': S, './fs': FS, './remote': R };
const fakeRequire = (n) => { if (!(n in mods)) throw new Error('harness: unexpected require ' + n); return mods[n]; };

const docA = () => app.documents[0], docB = () => app.documents[1];
function openDocs(posterA) {
  app.documents = [makeDoc(1, 'Show (2020) {tmdb-1}', posterA), makeDoc(2, 'Other (2021) {tmdb-2}', [lyr('show', true), lyr('s1'), lyr('s2')])];
  app.activeDocument = app.documents[0];
}
openDocs([lyr('poster art', true)]);
new Function('require', 'document', 'window', 'localStorage', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'console',
  fs.readFileSync(path.join(root, 'main.js'), 'utf8'))(fakeRequire, document, window, window.localStorage, fakeSetInterval, () => {}, fakeSetTimeout, clearTimeout, quietConsole);
const every3s = intervals.filter((i) => i.ms === 3000);   // the backdrop waits' check, then the queue poller
const waitPoll = every3s[0], queuePoll = every3s.slice(1);
const docPoll = intervals.filter((i) => i.ms === 2000);

// ---- helpers over the panel ----
const plain = () => Array.from(document.querySelectorAll('.notes .msg:not(.ask)')).map((d) => d.textContent);
const asks = () => Array.from(document.querySelectorAll('.notes .ask')).map((d) => d.textContent);
const alerts = () => Array.from(document.querySelectorAll('.notes .alert .alerttext')).map((d) => d.textContent);
const allNotes = () => Array.from(document.querySelectorAll('.notes .msg, .notes .alert .alerttext')).map((d) => d.textContent).join('\n');
const batchMsg = () => document.querySelector('.batchmsg').textContent;
const chipByText = (t) => Array.from(document.querySelectorAll('.chip')).find((c) => c.textContent === t);
const rightClick = (el, shiftKey = false) => el.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, shiftKey }));
const click = (sel) => document.querySelector(sel).dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const dismissFirstAlert = () => click('.notes .alert sp-action-button');
// A clean start for each case: two open PSDs (A active, with the given POSTER layers), nothing waiting, no notes.
async function reset(posterA) {
  await settle();
  if (!document.querySelector('.batchbar').classList.contains('hidden')) { click('[data-act="batch-cancel"]'); await settle(); }
  document.querySelector('.notes').innerHTML = '';
  calls = []; jpgLog = []; edits = []; coverageTargets = []; clockAhead += 60000; modals = 0; modalHooks.length = 0; glimpses.length = 0; onNextLook = null;
  [afterHooks, counts, outcome].forEach((o) => Object.keys(o).forEach((k) => delete o[k]));
  remote.on = false; remote.queue = []; remote.polls = 0; remote.fail = '';
  server.calls = []; server.claim = { status: 'none' }; server.fail = {}; server.files = []; server.folders = 2; server.unwanted = null; server.idarr = false;
  server.background = false; server.puts = []; local.picked = true; local.files = []; saves = [];
  Array.from(document.querySelectorAll('[data-wait-stop]')).forEach((b) => b.dispatchEvent(new window.MouseEvent('click', { bubbles: true })));   // no wait outlives its case
  document.querySelector('.notes').innerHTML = '';
  openDocs(posterA);
  await docPoll[0].fn(); await settle();   // the panel notices the active document
  click('[data-act="refresh"]'); await settle();
}
const onB = () => calls.filter((c) => c.doc === 2).map((c) => c.fn);
const count = (fn) => calls.filter((c) => c.fn === fn).length;
const switchToB = () => { app.activeDocument = docB(); };

(async () => {
  await settle();
  ok(every3s.length === 2 && queuePoll.length === 1 && docPoll.length === 1, 'found the backdrop check, the queue poller and the document poll');

  // ---- a finish or batch stays on the PSD it started on ----
  // A. MOVIE finish, nothing switches
  await reset([lyr('poster art', true)]);
  rightClick(chipByText('MOVIE')); await settle();
  ok(onB().length === 0, 'A: an undisturbed MOVIE finish never touches the other PSD');
  ok(count('savePsd') === 1 && count('exportJpg') === 1 && count('exportLogoPng') === 1 && count('makeCropDoc') === 1, 'A: save, JPG, logo PNG and Square Art all ran (' + calls.map((c) => c.fn).join(',') + ')');
  ok(/MOVIE: .*opening Square Art/.test(allNotes()) && !/another document/.test(allNotes()), 'A: the usual report, no stop message');
  ok(docA().layers[1].layers[0].name === 'main', 'A: the poster layer was renamed to main');

  // B. switch right after trim (before the save)
  await reset([lyr('poster art', true)]);
  after('trim', 1, switchToB);
  rightClick(chipByText('MOVIE')); await settle();
  ok(onB().length === 0, 'B: nothing ran on the other PSD (got ' + onB() + ')');
  ok(count('savePsd') === 0 && count('exportJpg') === 0 && count('makeCropDoc') === 0, 'B: no save, no JPG, no Square Art after the switch');
  ok(/saved: another document became active, so the finish stopped/.test(allNotes()) && /STOPPED, PSD not saved/.test(allNotes()), 'B: the report says why and that the PSD was not saved: ' + allNotes());

  // C. switch right after the save
  await reset([lyr('poster art', true)]);
  after('savePsd', 1, switchToB);
  rightClick(chipByText('MOVIE')); await settle();
  ok(onB().length === 0 && count('exportJpg') === 0 && count('exportLogoPng') === 0 && count('makeCropDoc') === 0, 'C: nothing exported from either PSD after the switch');
  ok(/coverage: another document became active, so the finish stopped  ·  JPG and logo PNG and Square Art skipped/.test(allNotes()), 'C: the report names the step it stopped at: ' + allNotes());

  // D. switch after the last export: Square Art must not open on the other PSD
  await reset([lyr('poster art', true)]);
  after('exportLogoPng', 1, switchToB);
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('makeCropDoc') === 0 && onB().length === 0, 'D: Square Art did not open on the other PSD');
  ok(/Square Art: another document became active/.test(allNotes()), 'D: reported: ' + allNotes());

  // E. another PSD is active for one moment only (away and straight back): the finish still ends there
  await reset([lyr('poster art', true)]);
  afterModal(1, () => { glimpses.push(docB()); });   // after the "main" rename, the next look sees the other PSD
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('trim') === 0 && count('savePsd') === 0 && count('exportJpg') === 0 && onB().length === 0, 'E: once another PSD was seen, no later step runs, even back on the first PSD (' + calls.map((c) => c.fn).join(',') + ')');
  ok(/logo name: another document became active, so the finish stopped/.test(allNotes()) && /STOPPED, PSD not saved/.test(allNotes()), 'E: reported: ' + allNotes());

  // F. orange SHOW finish: switch during the tag batch (after the 2nd JPG)
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  after('exportJpg', 2, switchToB);
  rightClick(chipByText('SHOW')); await settle();
  ok(count('exportJpg') === 2 && onB().length === 0, 'F: the batch exported 2 JPGs from its own PSD and nothing from the other (' + count('exportJpg') + ', B: ' + onB() + ')');
  ok(count('exportLogoPng') === 0 && count('makeCropDoc') === 0, 'F: the rest of the finish did not run');
  ok(/tag batch: another document became active; the batch stopped after 2 JPGs/.test(allNotes()), 'F: the finish report carries the batch stop: ' + allNotes());
  ok(!/Batch stopped:/.test(allNotes()), 'F: no second "Batch stopped" note inside a finish');

  // G. orange SHOW finish, nothing switches
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  rightClick(chipByText('SHOW')); await settle();
  ok(count('exportJpg') === 3 && count('exportLogoPng') === 1 && count('savePsd') === 2 && count('makeCropDoc') === 1 && onB().length === 0, 'G: undisturbed orange SHOW: 3 JPGs, logo PNG, two saves, Square Art (' + calls.map((c) => c.fn).join(',') + ')');

  // H. orange batch outside a finish: a switch mid-run leaves a note that outlives the batch bar
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  after('exportJpg', 1, switchToB);
  click('[data-act="batch-run"]'); await settle();
  ok(count('exportJpg') === 1 && onB().length === 0, 'H: the batch stopped after 1 JPG, nothing on the other PSD (' + count('exportJpg') + ', B: ' + onB() + ')');
  ok(/Batch stopped: another document became active; the batch stopped after 1 JPG$/m.test(allNotes()), 'H: a note outlives the batch bar: ' + allNotes());

  // I. collection export: switch while the temporary logo is placed
  await reset([lyr('c', true)]);
  click('[data-act="batch"]'); await settle();
  after('placeCollectionLogo', 1, switchToB);
  click('[data-act="batch-run"]'); await settle();
  ok(count('placeCollectionLogo') === 1 && count('exportJpg') === 0, 'I: no JPG is exported once the PSD changed under the placed logo');
  ok(count('removeCollectionLogo') === 0 && onB().length === 0, 'I: the logo cleanup did not run on the other PSD (B: ' + onB() + ')');
  ok(/temporary collection logo is still in the PSD/.test(allNotes()), 'I: the leftover is explained: ' + allNotes());

  // J. collection export, nothing switches: the cleanup runs
  await reset([lyr('c', true)]);
  click('[data-act="batch"]'); await settle();
  click('[data-act="batch-run"]'); await settle();
  ok(count('placeCollectionLogo') === 1 && count('exportJpg') === 1 && count('removeCollectionLogo') === 1, 'J: undisturbed collection export places, exports and cleans up');

  // K. blue SHOW finish waiting for Run: Run from another PSD is refused; back on its PSD it works
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW'), true); await settle();
  ok(/Type the seasons and press Run/.test(allNotes()), 'K: the blue finish is waiting for Run: ' + allNotes());
  switchToB(); await docPoll[0].fn(); await settle();
  document.querySelector('.batchinput').value = '2';
  click('[data-act="batch-run"]'); await settle();
  ok(count('exportJpg') === 0, 'K: Run on another PSD exports nothing');
  ok(/waiting on another PSD/.test(batchMsg()), 'K: and says so: ' + batchMsg());
  app.activeDocument = docA(); await docPoll[0].fn(); await settle();
  document.querySelector('.batchinput').value = '2';
  click('[data-act="batch-run"]'); await settle();
  ok(count('exportJpg') === 2 && onB().length === 0, 'K: back on its PSD, Run exports the 2 seasons (' + count('exportJpg') + ')');
  ok(/season batch \(2 JPGs\)/.test(allNotes()) && count('makeCropDoc') === 1, 'K: and the finish carries on to the end: ' + allNotes());

  // L. the queue waits for a running finish, then opens the PSD
  await reset([lyr('poster art', true)]);
  remote.on = true; remote.queue = [{ filename: 'New.psd', name: 'New (2024)', style: 'CL2K' }];
  after('trim', 1, () => { onNextLook = () => { queuePoll[0].fn(); }; });   // the 3 s queue check lands in the gap after trim
  rightClick(chipByText('MOVIE')); await settle();
  ok(remote.polls === 0 && count('openQueued') === 0, 'L: the queue was not polled mid-finish (polls: ' + remote.polls + ')');
  ok(count('savePsd') === 1 && count('exportJpg') === 1 && /opening Square Art/.test(allNotes()), 'L: the finish ran to the end on its PSD');
  await queuePoll[0].fn(); await settle();
  ok(remote.polls === 1 && count('openQueued') === 1, 'L: after the finish the queued PSD opens');

  // M. a blue finish waiting for Run does not hold the queue
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW'), true); await settle();
  remote.on = true; remote.queue = [{ filename: 'New.psd', name: 'New (2024)', style: 'CL2K' }];
  await queuePoll[0].fn(); await settle();
  ok(remote.polls === 1 && count('openQueued') === 1, 'M: the queue still opens PSDs while a blue finish waits for Run');
  click('[data-act="batch-run"]'); await settle();
  ok(count('exportJpg') === 0 && /waiting on another PSD/.test(batchMsg()), 'M: and Run on the newly opened PSD is refused');

  // N. Run with no document open
  await reset([lyr('show', true), lyr('s1')]);
  click('[data-act="batch"]'); await settle();
  app.documents = []; app.activeDocument = null;
  click('[data-act="batch-run"]'); await settle();
  ok(/No document open/.test(batchMsg()) && count('exportJpg') === 0, 'N: Run with no document says so and does nothing: ' + batchMsg());

  // ---- failures are red notes that stay; everything else is a plain note that expires ----
  // O. a clean finish is a plain note; a stopped one is red and outlives the 15 s expiry
  await reset([lyr('poster art', true)]);
  rightClick(chipByText('MOVIE')); await settle();
  ok(alerts().length === 0 && plain().some((t) => /^MOVIE: /.test(t)), 'O: a clean finish reports as a plain note');
  await reset([lyr('poster art', true)]);
  after('savePsd', 1, switchToB);
  rightClick(chipByText('MOVIE')); await settle();
  ok(alerts().length === 1 && /^MOVIE: .*coverage: another document/.test(alerts()[0]), 'O: a stopped finish reports as one red note: ' + alerts());
  app.activeDocument = docA(); await docPoll[0].fn(); await settle();
  expireNotes();
  ok(alerts().length === 1 && plain().length === 0, 'O: after 15 s the plain notes are gone and the red one is still there (' + plain().length + ' plain, ' + alerts().length + ' red)');
  dismissFirstAlert();
  ok(alerts().length === 0, 'O: its dismiss button removes it');

  // P. an unexpected error is red; hints stay plain
  await reset([lyr('poster art', true)]);
  outcome.savePsd = new Error('disk full');
  click('[data-act="save"]'); await settle();
  ok(alerts().join() === 'Error: disk full', 'P: a failed save is a red "Error:" note: ' + alerts());
  await reset([lyr('poster art', true)]);
  outcome.exportLogoPng = { ok: false, reason: 'empty' };
  click('[data-act="logo-export"]'); await settle();
  ok(alerts().length === 0 && /no visible pixels/.test(plain().join()), 'P: "LOGO group has no visible pixels" stays a plain hint: ' + allNotes());
  outcome.exportLogoPng = { ok: false, reason: 'error' };
  click('[data-act="logo-export"]'); await settle();
  ok(alerts().join() === 'Logo export failed.', 'P: a plain logo export failure is red: ' + alerts());
  await reset([lyr('poster art', true)]);
  click('[data-act="squareart"]'); await settle();   // the stub answers "no POSTER group"
  ok(alerts().length === 0 && /No POSTER group/.test(plain().join()), 'P: "No POSTER group" stays a plain hint');
  outcome.makeCropDoc = { ok: false, reason: 'error' };
  click('[data-act="squareart"]'); await settle();
  ok(alerts().join() === 'Square Art failed.', 'P: a plain Square Art failure is red: ' + alerts());

  // Q. at most five red notes, newest on top
  await reset([lyr('poster art', true)]);
  for (let i = 1; i <= 7; i++) { outcome.savePsd = new Error('boom ' + i); click('[data-act="save"]'); await settle(); }
  ok(alerts().length === 5 && alerts()[0] === 'Error: boom 7' && alerts()[4] === 'Error: boom 3', 'Q: five kept, newest first, oldest dropped: ' + alerts());

  // R. a batch stopped outside a finish, and the leftover collection logo, are red
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  after('exportJpg', 1, switchToB);
  click('[data-act="batch-run"]'); await settle();
  ok(alerts().some((t) => /^Batch stopped: another document became active/.test(t)), 'R: a stopped batch is a red note: ' + alerts());
  await reset([lyr('c', true)]);
  click('[data-act="batch"]'); await settle();
  after('placeCollectionLogo', 1, switchToB);
  click('[data-act="batch-run"]'); await settle();
  ok(alerts().some((t) => /temporary collection logo is still in the PSD.*__PFLTMPLOGO__/.test(t)), 'R: the leftover collection logo is a red note naming the layer: ' + alerts());

  // S. the link error: one red note per error, replaced when it changes, gone when the link works
  await reset([lyr('poster art', true)]);
  remote.on = true; remote.fail = 'HTTP 500';
  await queuePoll[0].fn(); await queuePoll[0].fn(); await queuePoll[0].fn(); await settle();
  ok(alerts().join() === 'Posterflow link: HTTP 500', 'S: three failed checks in a row show one red note: ' + alerts());
  remote.fail = 'HTTP 401';
  await queuePoll[0].fn(); await settle();
  ok(alerts().join() === 'Posterflow link: HTTP 401', 'S: a different error replaces it instead of stacking: ' + alerts());
  dismissFirstAlert();
  await queuePoll[0].fn(); await settle();
  ok(alerts().length === 0, 'S: once dismissed, the same error does not come back every 3 s');
  remote.fail = 'HTTP 500';
  await queuePoll[0].fn(); await settle();
  ok(alerts().length === 1, 'S: a new error shows again');
  remote.fail = '';
  await queuePoll[0].fn(); await settle();
  ok(alerts().length === 0, 'S: the red note clears itself when the link works again');
  remote.fail = 'HTTP 500';
  await queuePoll[0].fn(); await settle();
  ok(alerts().join() === 'Posterflow link: HTTP 500', 'S: and the next outage is reported again');

  // T. a PSD from Posterflow that fails to open is red
  await reset([lyr('poster art', true)]);
  remote.on = true; remote.queue = [{ filename: 'Broken.psd', name: 'Broken', style: 'CL2K' }];
  outcome.openQueued = new Error('download failed (HTTP 404)');
  await queuePoll[0].fn(); await settle();
  ok(alerts().join() === 'Could not open "Broken.psd": download failed (HTTP 404)', 'T: ' + alerts());

  // ---- the coverage step: blank canvas stops a finish before its exports ----
  const posterLayer = (i) => docA().layers[1].layers[i];
  const stepChip = (f, text) => Array.from(document.querySelectorAll('.optrow[data-steps="' + f + '"] .chip')).find((c) => c.textContent === text);
  const order = () => calls.map((c) => c.fn).join(',');

  // U. MOVIE: the visible poster is checked after the save and before the JPG
  await reset([lyr('poster art', true)]);
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('checkCoverage') === 1 && onB().length === 0, 'U: the check ran once, on the finish\'s PSD');
  ok(/savePsd,checkCoverage,exportJpg/.test(order()), 'U: after the save, before the JPG (' + order() + ')');
  ok(coverageTargets.length === 1 && coverageTargets[0].label === 'main' && coverageTargets[0].id === posterLayer(0).id, 'U: its target is the visible poster layer: ' + JSON.stringify(coverageTargets));
  ok(/✓ saved  ·  ✓ coverage  ·  ✓ JPG/.test(allNotes()), 'U: it shows in the checklist: ' + allNotes());

  // V. blank canvas: the finish stops, the PSD stays saved, nothing is exported, the report is red
  await reset([lyr('poster art', true)]);
  outcome.checkCoverage = { problems: ['main: blank canvas (~40 px above the bottom guide)'], boundsOnly: [] };
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('savePsd') === 1 && count('exportJpg') === 0 && count('exportLogoPng') === 0 && count('makeCropDoc') === 0, 'V: saved, then nothing exported (' + order() + ')');
  ok(alerts().length === 1 && /coverage: main: blank canvas \(~40 px above the bottom guide\)  ·  JPG and logo PNG and Square Art skipped/.test(alerts()[0]), 'V: one red report naming the layer and the gap: ' + alerts());

  // W. no horizontal guide: skipped, the finish carries on
  await reset([lyr('poster art', true)]);
  outcome.checkCoverage = { skipped: 'no horizontal guide' };
  rightClick(chipByText('MOVIE')); await settle();
  ok(/– coverage skipped \(no horizontal guide\)/.test(allNotes()) && count('exportJpg') === 1 && alerts().length === 0, 'W: reported as skipped and the JPG still exports: ' + allNotes());

  // X. pixels unreadable: passes on bounds and says so
  await reset([lyr('poster art', true)]);
  outcome.checkCoverage = { problems: [], boundsOnly: ['main'] };
  rightClick(chipByText('MOVIE')); await settle();
  ok(/✓ coverage \(bounds only: main\)/.test(allNotes()) && count('exportJpg') === 1, 'X: ' + allNotes());

  // Y. orange SHOW: every tagged layer is a target; blank canvas and tag problems come in one report
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  rightClick(chipByText('SHOW')); await settle();
  ok(coverageTargets.map((t) => t.label).join() === 'show,s1,s2' && coverageTargets.every((t, i) => t.id === posterLayer(i).id), 'Y: targets are the tagged POSTER layers themselves: ' + JSON.stringify(coverageTargets));
  ok(count('exportJpg') === 3 && /✓ coverage  ·  ✓ tag batch \(3 JPGs\)/.test(allNotes()), 'Y: a clean check lets the batch run');
  await reset([lyr('show', true), lyr('s1'), lyr('Layer 9')]);
  outcome.checkCoverage = { problems: ['s1: the layer is empty'], boundsOnly: [] };
  rightClick(chipByText('SHOW')); await settle();
  ok(count('exportJpg') === 0 && alerts().length === 1 && /coverage: s1: the layer is empty/.test(alerts()[0]) && /STOPPED\. Fix these tags.*Layer 9/.test(alerts()[0]), 'Y: both problems in one red report, nothing exported: ' + alerts());
  ok(coverageTargets.map((t) => t.label).join() === 'show,s1', 'Y: an untagged layer is not a coverage target');

  // Z. COLLECTION checks the c layer; blue SHOW checks the visible poster before it asks for seasons
  await reset([lyr('c', true), lyr('s1')]);
  rightClick(chipByText('COLLECTION')); await settle();
  ok(coverageTargets.length === 1 && coverageTargets[0].label === 'c' && coverageTargets[0].id === posterLayer(0).id, 'Z: COLLECTION target: ' + JSON.stringify(coverageTargets));
  await reset([lyr('show', true)]);
  outcome.checkCoverage = { problems: ['show: see-through area inside the poster (~900 px, x 300 to 420, y 1300 to 1374)'], boundsOnly: [] };
  rightClick(chipByText('SHOW'), true); await settle();
  ok(coverageTargets[0].label === 'show' && !/Type the seasons/.test(allNotes()) && document.querySelector('.batchbar').classList.contains('hidden'), 'Z: blue SHOW stops before the range box');
  ok(alerts().length === 1 && /see-through area inside the poster/.test(alerts()[0]), 'Z: with a red report: ' + alerts());

  // AA. switched off in the gear settings: not run, not in the checklist
  await reset([lyr('poster art', true)]);
  stepChip('movie', 'coverage').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await settle();
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('checkCoverage') === 0 && !/coverage/.test(allNotes()) && count('exportJpg') === 1, 'AA: the step is skipped silently when switched off: ' + allNotes());
  stepChip('movie', 'coverage').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await settle();
  await reset([lyr('poster art', true)]);
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('checkCoverage') === 1, 'AA: and runs again when switched back on');

  // ---- the JPG button: blank canvas holds the export back until JPG is pressed again ----
  const BLANK = { problems: ['main: blank canvas (~20 px at the left edge)'], boundsOnly: [] };
  // AB. a clean poster exports as before
  await reset([lyr('main', true)]);
  click('[data-act="jpg"]'); await settle();
  ok(count('checkCoverage') === 1 && coverageTargets[0].label === 'main' && count('exportJpg') === 1 && alerts().length === 0, 'AB: checked, then exported, no red note (' + order() + ')');
  ok(plain().some((t) => /^Saved "/.test(t)), 'AB: the usual Saved note');

  // AC. blank canvas: not exported; the second press exports without checking again
  await reset([lyr('main', true)]);
  outcome.checkCoverage = BLANK;
  click('[data-act="jpg"]'); await settle();
  ok(count('exportJpg') === 0, 'AC: the first press exports nothing');
  ok(alerts().join() === 'JPG not exported. main: blank canvas (~20 px at the left edge). Press JPG again to export anyway.', 'AC: the red note says why and how to go on: ' + alerts());
  click('[data-act="jpg"]'); await settle();
  ok(count('exportJpg') === 1 && count('checkCoverage') === 1, 'AC: the second press exports anyway, without another check (' + order() + ')');
  ok(alerts().length === 0, 'AC: and the red note is gone');
  click('[data-act="jpg"]'); await settle();
  ok(count('exportJpg') === 1 && count('checkCoverage') === 2 && alerts().length === 1, 'AC: a third press is checked afresh and held back again');

  // AD. the second press only counts for 10 s
  await reset([lyr('main', true)]);
  outcome.checkCoverage = BLANK;
  click('[data-act="jpg"]'); await settle();
  clockAhead += 11000;
  click('[data-act="jpg"]'); await settle();
  ok(count('exportJpg') === 0 && count('checkCoverage') === 2 && alerts().length === 1, 'AD: after 10 s a press is a first press again, with one red note (' + order() + ')');

  // AE. the hold belongs to the PSD it was raised on
  await reset([lyr('main', true)]);
  outcome.checkCoverage = BLANK;
  click('[data-act="jpg"]'); await settle();
  delete outcome.checkCoverage;
  switchToB(); await docPoll[0].fn(); await settle();
  click('[data-act="jpg"]'); await settle();
  ok(count('checkCoverage') === 2 && calls[calls.length - 1].fn === 'exportJpg' && calls[calls.length - 1].doc === 2, 'AE: another PSD gets its own check before its JPG (' + order() + ')');

  // AF. nothing to check, or a check that breaks: the JPG exports as before
  await reset([lyr('main', true)]);
  outcome.checkCoverage = { skipped: 'no horizontal guide' };
  click('[data-act="jpg"]'); await settle();
  ok(count('exportJpg') === 1 && alerts().length === 0, 'AF: no horizontal guide exports');
  await reset([lyr('main', true)]);
  outcome.checkCoverage = new Error('pixels exploded');
  click('[data-act="jpg"]'); await settle();
  ok(count('exportJpg') === 1 && alerts().length === 0, 'AF: a broken check exports');
  await reset([lyr('main', true)]);
  docA().layers.splice(1, 1);   // no POSTER group: not a poster PSD
  click('[data-act="refresh"]'); await settle();
  outcome.checkCoverage = BLANK;
  click('[data-act="jpg"]'); await settle();
  ok(count('checkCoverage') === 0 && count('exportJpg') === 1, 'AF: a document with no POSTER group is not checked (' + order() + ')');

  // AG. a finish's own JPG step is not held back by the button's check
  await reset([lyr('poster art', true)]);
  stepChip('movie', 'coverage').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await settle();
  outcome.checkCoverage = BLANK;
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('checkCoverage') === 0 && count('exportJpg') === 1, 'AG: with the finish step switched off, the finish exports its JPG unchecked (' + order() + ')');
  stepChip('movie', 'coverage').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await settle();

  // ---- a manual ⚡ Run: blank canvas holds the batch back until Run is pressed again ----
  const BLANK_S1 = { problems: ['s1: blank canvas (~40 px above the bottom guide)'], boundsOnly: [] };
  const typeRun = async (text) => { document.querySelector('.batchinput').value = text; click('[data-act="batch-run"]'); await settle(); };
  const labels = () => coverageTargets.map((t) => t.label).join();

  // AH. orange Run, clean: every tagged layer is checked, then exported
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  await typeRun('');
  ok(count('checkCoverage') === 1 && labels() === 'show,s1,s2' && count('exportJpg') === 3 && alerts().length === 0, 'AH: checked show,s1,s2 then exported 3 (' + labels() + '; ' + order() + ')');

  // AI. orange Run with blank canvas: held back, then Run again exports without another check
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  outcome.checkCoverage = BLANK_S1;
  await typeRun('');
  ok(count('exportJpg') === 0 && count('setLayersVisible') === 0, 'AI: nothing exported or toggled on the first Run');
  ok(alerts().join() === 'Batch not started. s1: blank canvas (~40 px above the bottom guide). Press Run again to export anyway.', 'AI: red note: ' + alerts());
  ok(/Press Run again to export anyway/.test(batchMsg()) && !document.querySelector('.batchrow').classList.contains('hidden'), 'AI: the batch bar says so and keeps Run available: ' + batchMsg());
  await typeRun('');
  ok(count('exportJpg') === 3 && count('checkCoverage') === 1 && alerts().length === 0, 'AI: the second Run exports all 3 with no second check, and the note is gone (' + order() + ')');

  // AJ. the filter decides which layers are checked
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  await typeRun('s1');
  ok(labels() === 's1' && count('exportJpg') === 1, 'AJ: filter "s1" checks and exports only s1 (' + labels() + ')');
  await reset([lyr('main', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  await typeRun('show');
  ok(labels() === 'main' && count('exportJpg') === 1, 'AJ: filter "show" matches the layer tagged main (' + labels() + ')');
  await reset([lyr('show', true), lyr('s1-2'), lyr('s3')]);
  click('[data-act="batch"]'); await settle();
  await typeRun('2');
  ok(labels() === 's1-2', 'AJ: filter "2" checks the range layer that makes Season 2 (' + labels() + ')');

  // AK. blue Run checks the visible poster; a changed range is a new press
  await reset([lyr('show', true)]);
  click('[data-act="batch-seasons"]'); await settle();
  outcome.checkCoverage = { problems: ['show: the layer is empty'], boundsOnly: [] };
  await typeRun('2');
  ok(labels() === 'show' && count('exportJpg') === 0 && alerts().length === 1, 'AK: blue Run held back on the visible poster (' + labels() + ')');
  await typeRun('3');
  ok(count('exportJpg') === 0 && count('checkCoverage') === 2 && alerts().length === 1, 'AK: a different range is checked again, still one red note');
  await typeRun('3');
  ok(count('exportJpg') === 3, 'AK: the same range again exports (' + count('exportJpg') + ')');

  // AL. inside a finish the batch is not checked a second time
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  rightClick(chipByText('SHOW')); await settle();
  ok(count('checkCoverage') === 1 && count('exportJpg') === 3, 'AL: an orange finish checks once (its own step)');
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW'), true); await settle();
  outcome.checkCoverage = { problems: ['show: the layer is empty'], boundsOnly: [] };   // would hold a manual Run back
  await typeRun('2');
  ok(count('checkCoverage') === 1 && count('exportJpg') === 2, 'AL: Run for a waiting blue finish goes straight to the batch (' + order() + ')');

  // ---- the blue ⚡ needs a typed range: blank never means every season the template has ----
  // AM. blank and unreadable input ask for a range, before any check or export
  await reset([lyr('show', true)]);
  click('[data-act="batch-seasons"]'); await settle();
  outcome.checkCoverage = { problems: ['show: the layer is empty'], boundsOnly: [] };   // would hold a real Run back
  await typeRun('');
  ok(/^Type the seasons first/.test(batchMsg()) && count('exportJpg') === 0 && count('checkCoverage') === 0 && alerts().length === 0, 'AM: a blank box asks for the seasons and does nothing else: ' + batchMsg());
  ok(!document.querySelector('.batchrow').classList.contains('hidden'), 'AM: the box stays open');
  await typeRun('abc');
  ok(/^Could not read "abc"/.test(batchMsg()) && count('exportJpg') === 0 && count('checkCoverage') === 0, 'AM: unreadable text says so: ' + batchMsg());
  delete outcome.checkCoverage;
  await typeRun('2');
  ok(count('exportJpg') === 2, 'AM: a typed range then runs (' + count('exportJpg') + ' JPGs)');

  // AN. a blue finish waiting for Run keeps waiting through a blank Run
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW'), true); await settle();
  await typeRun('');
  ok(/^Type the seasons first/.test(batchMsg()) && count('exportJpg') === 0 && !/season batch/.test(allNotes()), 'AN: the finish is still waiting: ' + batchMsg());
  await typeRun('3');
  ok(count('exportJpg') === 3 && /season batch \(3 JPGs\)/.test(allNotes()), 'AN: and finishes once a range is typed: ' + allNotes());

  // AO. the orange ⚡ is unchanged: blank still means every tagged layer
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  click('[data-act="batch"]'); await settle();
  await typeRun('');
  ok(count('exportJpg') === 3, 'AO: orange Run with a blank filter exports all 3 tagged layers');

  // ---- right-click LIMITED SERIES: the main JPG and " - Season 1" with the limited series text ----
  const BASE = 'Show (2020) {tmdb-1}';
  const made = () => jpgLog.map((j) => j.name.replace(BASE, '<name>') + ' from [' + j.poster + ']' + (j.cls ? ' +text' : '')).join(' | ');

  // AP. one untagged poster makes both JPGs; another POSTER layer stays hidden for both
  await reset([lyr('poster art', true), lyr('old idea')]);
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(made() === '<name> from [poster art] | <name> - Season 1 from [poster art] +text', 'AP: main then Season 1, same poster, text only on Season 1: ' + made());
  ok(coverageTargets.length === 1 && coverageTargets[0].label === 'poster art' && coverageTargets[0].id === posterLayer(0).id, 'AP: that poster is the one checked for blank canvas: ' + JSON.stringify(coverageTargets));
  ok(/^LIMITED SERIES: .*\u2713 saved  \u00b7  \u2713 coverage  \u00b7  \u2713 tag batch \(2 JPGs\)/m.test(allNotes()) && count('savePsd') === 2 && count('makeCropDoc') === 1 && alerts().length === 0, 'AP: the usual SHOW ending follows: ' + allNotes());
  ok(docA().layers[5].visible === false, 'AP: the limited series text is off again afterwards');

  // AQ. a poster tagged cls alone, or show alone, also makes both
  await reset([lyr('cls', true)]);
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(made() === '<name> from [cls] | <name> - Season 1 from [cls] +text', 'AQ: a lone cls layer: ' + made());
  await reset([lyr('main', true)]);
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(made() === '<name> from [main] | <name> - Season 1 from [main] +text', 'AQ: a lone main layer: ' + made());

  // AR. a show layer and a cls layer export as tagged
  await reset([lyr('show', true), lyr('cls')]);
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(made() === '<name> from [show] | <name> - Season 1 from [cls] +text', 'AR: each JPG from its own layer: ' + made());
  ok(labels() === 'show,cls', 'AR: both are checked for blank canvas (' + labels() + ')');

  // AS. a season layer stops it: saved, nothing exported, red report naming the layer
  await reset([lyr('show', true), lyr('s1'), lyr('s2-4')]);
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(count('savePsd') === 1 && count('exportJpg') === 0 && count('checkCoverage') === 0 && count('makeCropDoc') === 0, 'AS: saved, then nothing else (' + order() + ')');
  ok(alerts().length === 1 && /STOPPED: a limited series has no season posters, but POSTER has "s1", "s2-4"\. Rename or delete them/.test(alerts()[0]), 'AS: ' + alerts());

  // AT. blank canvas stops it before the exports; nothing visible stops it too
  await reset([lyr('poster art', true)]);
  outcome.checkCoverage = { problems: ['poster art: blank canvas (~40 px above the bottom guide)'], boundsOnly: [] };
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(count('exportJpg') === 0 && alerts().length === 1 && /coverage: poster art: blank canvas/.test(alerts()[0]), 'AT: ' + alerts());
  await reset([lyr('poster art', false)]);
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(count('exportJpg') === 0 && alerts().some((t) => /STOPPED: no visible poster in POSTER/.test(t)), 'AT: no visible poster: ' + alerts());

  // AU. a plain click on the chip still only toggles the text
  await reset([lyr('poster art', true)]);
  chipByText('LIMITED SERIES').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await settle();
  ok(docA().layers[5].visible === true && count('exportJpg') === 0 && count('savePsd') === 0, 'AU: click shows the text and exports nothing');

  // ---- orange SHOW with one poster: it waits for the seasons that poster should also make ----
  const shown = () => jpgLog.map((j) => j.name.replace(BASE, '<name>') + (j.season ? ' [' + j.season + ']' : '')).join(' | ');

  // AV. one show poster: the finish waits; a blank Run makes the main JPG and Season 1
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW')); await settle();
  ok(count('exportJpg') === 0 && count('savePsd') === 1 && count('checkCoverage') === 1, 'AV: saved and checked, nothing exported yet (' + order() + ')');
  ok(/^One poster\. Run exports the main JPG and Season 1/.test(batchMsg()) && /One poster: press Run for the main JPG and Season 1/.test(allNotes()), 'AV: the box and the report both say what Run will do: ' + batchMsg());
  await typeRun('');
  ok(shown() === '<name> | <name> - Season 1 [Season 1]', 'AV: main with no season text, then Season 1 with its text: ' + shown());
  ok(/tag batch \(2 JPGs\)/.test(allNotes()) && count('savePsd') === 2 && count('makeCropDoc') === 1 && count('checkCoverage') === 1, 'AV: then the usual ending, with no second blank canvas check: ' + allNotes());

  // AW. typed seasons: main plus those; 0 alone is main only
  await reset([lyr('main', true)]);
  rightClick(chipByText('SHOW')); await settle();
  await typeRun('3');
  ok(shown() === '<name> | <name> - Season 1 [Season 1] | <name> - Season 2 [Season 2] | <name> - Season 3 [Season 3]', 'AW: "3" makes main and Seasons 1 to 3, from a layer tagged main too: ' + shown());
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW')); await settle();
  await typeRun('0');
  ok(shown() === '<name>' && /tag batch \(1 JPG\)/.test(allNotes()), 'AW: "0" makes the main JPG only: ' + shown());

  // AX. unreadable text keeps it waiting; a cancel ends the finish and frees the panel
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW')); await settle();
  await typeRun('abc');
  ok(/^Could not read "abc"/.test(batchMsg()) && count('exportJpg') === 0, 'AX: unreadable text is refused: ' + batchMsg());
  click('[data-act="batch-cancel"]'); await settle();
  ok(plain().some((t) => /^SHOW: season batch cancelled; .*skipped\.$/.test(t)) && count('exportJpg') === 0, 'AX: cancel is reported: ' + plain());
  rightClick(chipByText('MOVIE')); await settle();
  ok(count('exportJpg') === 1, 'AX: and another finish can start afterwards');

  // AY. a second layer in POSTER means no waiting: the orange finish runs unattended
  await reset([lyr('show', true), lyr('c')]);
  rightClick(chipByText('SHOW')); await settle();
  ok(count('exportJpg') === 2 && !/One poster/.test(allNotes()) && /tag batch \(2 JPGs\)/.test(allNotes()), 'AY: show plus another tagged layer runs straight through (' + count('exportJpg') + ' JPGs)');

  // AZ. the orange bolt button itself does not wait: one tagged layer, one JPG
  await reset([lyr('show', true)]);
  click('[data-act="batch"]'); await settle();
  ok(/^Found 1 tagged export/.test(batchMsg()), 'AZ: the bolt shows the tag batch: ' + batchMsg());
  await typeRun('');
  ok(shown() === '<name>', 'AZ: and exports the one JPG: ' + shown());

  // BA. the layers changed while the box waited: Run says so and exports nothing
  await reset([lyr('show', true)]);
  rightClick(chipByText('SHOW')); await settle();
  docA().layers[1].layers.push(lyr('s1'));
  await typeRun('');
  ok(count('exportJpg') === 0 && /POSTER no longer holds a single show poster/.test(batchMsg()), 'BA: ' + batchMsg());

  // ---- upload and complete: through the Posterflow link, off unless switched on in the gear settings ----
  const ID = '11111111-2222-3333-4444-555555555555';
  const DISCORD = { will: 'post to Discord', aside: '', problem: '' };
  const MATCH = { status: 'match', type: 'request', where: 'requests', id: ID, label: 'The Spy (2019)', has_thread: true, upload: DISCORD };
  const ITEM = { status: 'match', type: 'list_item', where: 'list-items', id: ID, label: 'The Spy · list', has_thread: false,
    upload: { will: '', aside: '', problem: 'The Spy · list is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow' } };
  const sent = () => server.calls.join(' | ');
  const stepsOn = async (f, want) => {   // want: { upload: true, complete: false }
    for (const text of Object.keys(want)) { if (stepChip(f, text).classList.contains('on') !== want[text]) { stepChip(f, text).dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await settle(); } }
  };
  const communityNote = () => Array.from(document.querySelectorAll('.notes .msg, .notes .alert .alerttext')).map((d) => d.textContent).find((t) => /^(MOVIE|SHOW|COLLECTION) · /.test(t)) || '';

  // BB. off by default: a finish asks the server nothing
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH;
  ok(!stepChip('movie', 'upload').classList.contains('on') && !stepChip('show', 'complete').classList.contains('on'), 'BB: both steps start switched off');
  rightClick(chipByText('MOVIE')); await settle();
  ok(server.calls.length === 0 && communityNote() === '' && count('exportJpg') === 1, 'BB: nothing is asked of Posterflow (' + sent() + ')');

  // BC. both on, one matching request: look it up, send the JPG, upload, complete
  await stepsOn('movie', { upload: true, complete: true });
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH;
  rightClick(chipByText('MOVIE')); await settle();
  ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1} | PUT requests/<id>/posters/Show (2020) {tmdb-1}.jpg | POST requests/<id>/posters | POST requests/<id>/complete', 'BC: ' + sent());
  ok(communityNote() === 'MOVIE · Show (2020): ✓ upload (posted 1 poster to The Spy (2019))  ·  ✓ complete (The Spy (2019))' && alerts().length === 0, 'BC: its own plain note: ' + communityNote());
  ok(count('makeCropDoc') === 1 && /opening Square Art/.test(allNotes()), 'BC: Square Art still opens');
  // the same finish while Posterflow's Upload also adds to IDarr: the note says so, in Posterflow's words
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH; server.idarr = true;
  rightClick(chipByText('MOVIE')); await settle();
  ok(/✓ upload \(posted 1 poster to The Spy \(2019\) and added it to IDarr \(Posters\)\)/.test(communityNote()), 'BC: with IDarr on: ' + communityNote());

  // BD. answers that skip quietly: no match, Discord not connected, already completed, no id in the name
  for (const [answer, why] of [[{ status: 'none' }, 'none of your claimed requests matches "Show (2020)"'], [{ status: 'not_connected' }, 'Discord is not connected in Posterflow'],
    [{ status: 'done', label: 'The Spy (2019)' }, 'already completed: The Spy (2019)'], [{ status: 'no_id' }, 'the PSD name has no {tmdb-']]) {
    await reset([lyr('poster art', true)]);
    remote.on = true; server.claim = answer;
    rightClick(chipByText('MOVIE')); await settle();
    ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1}' && communityNote().indexOf('– upload skipped (' + why) > 0 && /– complete skipped/.test(communityNote()) && alerts().length === 0, 'BD: ' + answer.status + ' skips both, in grey: ' + communityNote());
  }

  // BE. answers that stop in red: several matches, an expired login
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = { status: 'several', type: 'request', labels: ['Dark (2017) S2', 'Dark (2017) S3'] };
  rightClick(chipByText('MOVIE')); await settle();
  ok(server.calls.length === 1 && alerts().some((t) => /upload: 2 claimed requests match \(Dark \(2017\) S2, Dark \(2017\) S3\)\. Finish this one in Posterflow  ·  complete skipped/.test(t)), 'BE: several: ' + alerts());
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = { status: 'expired' };
  rightClick(chipByText('MOVIE')); await settle();
  ok(server.calls.length === 1 && alerts().some((t) => /upload: the Discord login in Posterflow has expired/.test(t)), 'BE: expired: ' + alerts());

  // BF. a failed upload is red and the request is NOT completed
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH; server.fail.post = 'Discord post failed at message 1 of 1: boom';
  rightClick(chipByText('MOVIE')); await settle();
  ok(!/complete$/.test(sent()) && alerts().some((t) => /✗ upload: Discord post failed at message 1 of 1: boom  ·  complete skipped/.test(t)), 'BF: ' + sent() + ' / ' + alerts());

  // BG. a list item with nothing to upload to: nothing is sent, the item is completed
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = ITEM;
  rightClick(chipByText('MOVIE')); await settle();
  ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1} | POST list-items/<id>/complete' && /upload skipped \(The Spy · list is a list item\. It has no Discord thread, and Adds to IDarr is off in Posterflow\)  ·  ✓ complete/.test(communityNote()), 'BG: ' + sent() + ' / ' + communityNote());
  // with IDarr on in Posterflow the list item's JPG is sent, under list-items
  await reset([lyr('poster art', true)]);
  remote.on = true; server.idarr = true; server.claim = Object.assign({}, ITEM, { upload: { will: 'add to IDarr (Posters)', aside: '', problem: '' } });
  rightClick(chipByText('MOVIE')); await settle();
  ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1} | PUT list-items/<id>/posters/Show (2020) {tmdb-1}.jpg | POST list-items/<id>/posters | POST list-items/<id>/complete', 'BG: a list item goes to IDarr: ' + sent());

  // BH. a request with nothing to upload to: nothing sent, so nothing completed
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = Object.assign({}, MATCH, { has_thread: false, upload: { will: '', aside: '', problem: 'The Spy (2019) has no Discord thread' } });
  rightClick(chipByText('MOVIE')); await settle();
  ok(server.calls.length === 1 && /upload skipped \(The Spy \(2019\) has no Discord thread\)  ·  – complete skipped \(nothing was uploaded\)/.test(communityNote()), 'BH: ' + communityNote());

  // BI. only complete switched on: the request is completed without an upload
  await stepsOn('movie', { upload: false, complete: true });
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH;
  rightClick(chipByText('MOVIE')); await settle();
  ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1} | POST requests/<id>/complete' && !/upload/.test(communityNote()), 'BI: ' + sent());
  await stepsOn('movie', { upload: true, complete: true });

  // BJ. no Posterflow link: both skip without asking anything
  await reset([lyr('poster art', true)]);
  remote.on = false; server.claim = MATCH;
  rightClick(chipByText('MOVIE')); await settle();
  ok(server.calls.length === 0 && /upload skipped \(no Posterflow link/.test(communityNote()), 'BJ: ' + communityNote());

  // BK. a finish that stops before its exports never reaches the community steps, and says so
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH;
  outcome.checkCoverage = { problems: ['main: blank canvas (~40 px above the bottom guide)'], boundsOnly: [] };
  rightClick(chipByText('MOVIE')); await settle();
  ok(server.calls.length === 0 && alerts().some((t) => /JPG and logo PNG and upload and complete and Square Art skipped/.test(t)), 'BK: ' + alerts());
  await stepsOn('movie', { upload: false, complete: false });

  // BL. an orange SHOW finish sends every JPG it exported, in export order, with kind=show
  await stepsOn('show', { upload: true, complete: true });
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  remote.on = true; server.claim = MATCH;
  rightClick(chipByText('SHOW')); await settle();
  ok(sent() === 'GET claim?kind=show&name=Show (2020) {tmdb-1} | PUT requests/<id>/posters/Show (2020) {tmdb-1}.jpg | PUT requests/<id>/posters/Show (2020) {tmdb-1} - Season 1.jpg | PUT requests/<id>/posters/Show (2020) {tmdb-1} - Season 2.jpg | POST requests/<id>/posters | POST requests/<id>/complete', 'BL: ' + sent());
  ok(/^SHOW · Show \(2020\): ✓ upload \(posted 3 posters to The Spy \(2019\)\)/.test(communityNote()), 'BL: ' + communityNote());
  // a LIMITED SERIES finish uses the SHOW row and looks its claim up as a show
  await reset([lyr('poster art', true)]);
  remote.on = true; server.claim = MATCH;
  rightClick(chipByText('LIMITED SERIES')); await settle();
  ok(/^GET claim\?kind=show&/.test(sent()) && server.calls.filter((c) => /^PUT/.test(c)).length === 2, 'BL: limited series sends its two JPGs (' + sent() + ')');

  // BM. the finish uploads only what the request asks for, and says what was left out
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  remote.on = true; server.claim = MATCH; server.unwanted = / - Season 1\.jpg$|\{tmdb-1\}\.jpg$/;
  rightClick(chipByText('SHOW')); await settle();
  ok(/✓ upload \(posted 1 poster to The Spy \(2019\); 2 not requested\)  ·  ✓ complete/.test(communityNote()), 'BM: ' + communityNote());
  await reset([lyr('show', true), lyr('s1'), lyr('s2')]);
  remote.on = true; server.claim = MATCH; server.unwanted = /./;
  rightClick(chipByText('SHOW')); await settle();
  ok(/– upload skipped \(The Spy \(2019\) asks for none of the 3 JPGs this finish exported\)  ·  – complete skipped \(nothing was uploaded\)/.test(communityNote()) && !/complete$/.test(sent()), 'BM: nothing requested, nothing completed: ' + communityNote());
  await stepsOn('show', { upload: false, complete: false });

  // ---- the Upload and Complete buttons: two presses each, only with the Posterflow link ----
  const cmBar = document.querySelector('.cmbar'), presets = document.querySelector('.sqpresets');
  const linkOn = async (on) => { remote.on = on; click('[data-act="link-save"]'); await settle(); document.querySelector('.notes').innerHTML = ''; };
  const FILES = [{ name: 'Show (2020) {tmdb-1}.jpg', poster: 'main', age: '2m ago' }, { name: 'Show (2020) {tmdb-1} - Season 2.jpg', poster: 'Season 2', age: '3d ago' }];
  ok(document.querySelector('[data-act="upload"]').textContent === 'Upload' && !document.querySelector('[data-act="discord"]'), 'the button is named after the one in Posterflow');
  ok(cmBar.classList.contains('trimbar') && !cmBar.closest('.sqbar') && cmBar.querySelectorAll('sp-action-button').length === 2
    && cmBar.previousElementSibling.classList.contains('sqbar') && cmBar.nextElementSibling.classList.contains('waits') && cmBar.nextElementSibling.nextElementSibling.classList.contains('notes'), 'Upload and Complete have a bar of their own, between Square Art and the waiting notes and messages');

  // BN. the bar shows with the link; the Square Art bar is as it always was
  await reset([lyr('poster art', true)]);
  await linkOn(false);
  ok(cmBar.classList.contains('hidden') && presets.classList.contains('hidden'), 'BN: no link: no Upload / Complete bar');
  await linkOn(true);
  ok(!cmBar.classList.contains('hidden') && presets.classList.contains('hidden'), 'BN: with the link the bar shows; the preset row still only keeps its space');
  outcome.makeCropDoc = { ok: true, doc: { id: 77 }, width: 950, height: 1425 };
  click('[data-act="squareart"]'); await settle();
  ok(!presets.classList.contains('hidden') && !cmBar.classList.contains('hidden'), 'BN: cropping: the presets show in the Square Art bar, the Upload / Complete bar stays put');
  click('[data-act="upload"]'); await settle();
  ok(server.calls.length === 0 && plain().some((t) => /^Upload: busy/.test(t)), 'BN: a press while cropping does nothing');
  click('[data-act="sq-cancel"]'); await settle();
  ok(presets.classList.contains('hidden') && !cmBar.classList.contains('hidden'), 'BN: after the crop the presets hide again');

  // BO. Upload: the first press shows the pick and what will happen, the second does it
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = MATCH; server.files = FILES;
  click('[data-act="upload"]'); await settle();
  ok(sent() === 'GET upload?name=Show (2020) {tmdb-1}', 'BO: the first press only asks: ' + sent());
  ok(asks().join() === 'Upload: 2 posters for The Spy (2019): main (2m ago), Season 2 (3d ago). Press Upload again to post to Discord.' && alerts().length === 0, 'BO: a blue note lists the pick: ' + asks());
  click('[data-act="upload"]'); await settle();
  ok(sent() === 'GET upload?name=Show (2020) {tmdb-1} | POST requests/<id>/upload', 'BO: the second press uploads: ' + sent());
  ok(asks().length === 0 && plain().indexOf('Upload: posted 2 posters to The Spy (2019).') >= 0, 'BO: the question goes and the upload is reported: ' + plain());
  click('[data-act="upload"]'); await settle();
  ok(server.calls.length === 3 && /^GET upload/.test(server.calls[2]) && asks().length === 1, 'BO: a third press is a first press again');
  // what the press will do, and did, comes from Posterflow's own upload settings
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.idarr = true; server.files = FILES;
  server.claim = Object.assign({}, MATCH, { has_thread: false, upload: { will: 'add to IDarr (Posters)', aside: 'No Discord thread, so nothing goes to Discord', problem: '' } });
  click('[data-act="upload"]'); await settle();
  ok(asks().join() === 'Upload: 2 posters for The Spy (2019): main (2m ago), Season 2 (3d ago). Press Upload again to add to IDarr (Posters). No Discord thread, so nothing goes to Discord.', 'BO: the settings and what cannot run are spelled out: ' + asks());
  click('[data-act="upload"]'); await settle();
  ok(plain().indexOf('Upload: posted 2 posters to The Spy (2019) and added them to IDarr (Posters).') >= 0, 'BO: the result is Posterflow\'s own wording: ' + plain());
  // a list item is uploaded under list-items
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.idarr = true; server.files = FILES; server.claim = Object.assign({}, ITEM, { upload: { will: 'add to IDarr (Posters)', aside: '', problem: '' } });
  click('[data-act="upload"]'); await settle();
  click('[data-act="upload"]'); await settle();
  ok(/POST list-items\/<id>\/upload$/.test(sent()), 'BO: a list item: ' + sent());

  // BP. the second press only counts for 10 s, and only on the PSD it was raised on
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = MATCH; server.files = FILES;
  click('[data-act="upload"]'); await settle();
  clockAhead += 11000;
  click('[data-act="upload"]'); await settle();
  ok(server.calls.every((c) => /^GET upload/.test(c)) && server.calls.length === 2 && asks().length === 1, 'BP: after 10 s nothing is uploaded, the pick is shown afresh (' + sent() + ')');
  switchToB();
  click('[data-act="upload"]'); await settle();
  ok(server.calls.length === 3 && server.calls[2] === 'GET upload?name=Other (2021) {tmdb-2}', 'BP: on another PSD the press asks about that PSD: ' + server.calls[2]);

  // BQ. nothing to upload: a plain note says why, and the next press starts over
  for (const [setup, why] of [
    [() => { server.claim = { status: 'none' }; }, 'Upload: none of your claimed requests matches "Show (2020)".'],
    [() => { server.claim = { status: 'not_connected' }; }, 'Upload: Discord is not connected in Posterflow.'],
    [() => { server.claim = { status: 'done', label: 'The Spy (2019)' }; }, 'Upload: already completed: The Spy (2019).'],
    [() => { server.claim = ITEM; }, 'Upload: The Spy · list is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow.'],
    [() => { server.claim = Object.assign({}, MATCH, { upload: { will: '', aside: '', problem: 'IDarr has no poster drive in Posterflow' } }); }, 'Upload: IDarr has no poster drive in Posterflow.'],
    [() => { server.claim = MATCH; server.folders = 0; }, 'Upload: no export folder is set in Posterflow.'],
    [() => { server.claim = MATCH; server.files = []; }, 'Upload: no export for The Spy (2019) in the Posterflow export folders.'],
  ]) {
    await reset([lyr('poster art', true)]);
    await linkOn(true); setup();
    click('[data-act="upload"]'); await settle();
    click('[data-act="upload"]'); await settle();
    ok(plain().filter((t) => t === why).length === 2 && asks().length === 0 && alerts().length === 0 && server.calls.every((c) => /^GET/.test(c)), 'BQ: ' + why + ' (' + plain() + ')');
  }

  // BR. answers and failures that need attention are red
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = { status: 'several', type: 'request', labels: ['Dark (2017) S2', 'Dark (2017) S3'] };
  click('[data-act="upload"]'); await settle();
  ok(alerts().join() === 'Upload: 2 claimed requests match (Dark (2017) S2, Dark (2017) S3). Finish this one in Posterflow.', 'BR: several: ' + alerts());
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = MATCH; server.files = FILES; server.fail.post = 'Discord post failed at message 1 of 1: boom';
  click('[data-act="upload"]'); await settle();
  click('[data-act="upload"]'); await settle();
  ok(alerts().join() === 'Upload: Discord post failed at message 1 of 1: boom.' && !plain().some((t) => /posted/.test(t)), 'BR: a failed upload: ' + alerts());

  // BS. Complete: the first press names the request, the second completes it
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = MATCH;
  click('[data-act="complete"]'); await settle();
  ok(sent() === 'GET claim?name=Show (2020) {tmdb-1}' && asks().join() === 'Complete: The Spy (2019). Press Complete again to mark it complete.', 'BS: the first press asks with no kind and names the request: ' + sent() + ' / ' + asks());
  click('[data-act="complete"]'); await settle();
  ok(sent() === 'GET claim?name=Show (2020) {tmdb-1} | POST requests/<id>/complete' && plain().indexOf('Complete: The Spy (2019) is marked complete.') >= 0 && asks().length === 0, 'BS: the second press completes it: ' + sent());
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = ITEM;
  click('[data-act="complete"]'); await settle();
  click('[data-act="complete"]'); await settle();
  ok(/POST list-items\/<id>\/complete$/.test(sent()), 'BS: a list item is completed as a list item: ' + sent());
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = MATCH; server.fail.complete = 'Only in-progress requests can be marked as complete';
  click('[data-act="complete"]'); await settle();
  click('[data-act="complete"]'); await settle();
  ok(alerts().join() === 'Complete: Only in-progress requests can be marked as complete.', 'BS: a refusal is red: ' + alerts());

  // BT. the two buttons keep their own questions, and an Upload press never completes anything
  await reset([lyr('poster art', true)]);
  await linkOn(true); server.claim = MATCH; server.files = FILES;
  click('[data-act="upload"]'); await settle();
  click('[data-act="complete"]'); await settle();
  ok(asks().length === 2 && server.calls.every((c) => /^GET/.test(c)), 'BT: one press of each only asks (' + sent() + ')');
  click('[data-act="upload"]'); await settle();
  ok(server.calls.filter((c) => /^POST/.test(c)).join() === 'POST requests/<id>/upload' && asks().join() === 'Complete: The Spy (2019). Press Complete again to mark it complete.', 'BT: Upload uploads; the Complete question still waits');
  await linkOn(false);


  // ---- Edit in tab / Send back ----
  const POSTER = { left: -80, top: 25, right: 1080, bottom: 1450 }, NARROW = { left: 300, top: 900, right: 700, bottom: 1100 };
  // Layers as Photoshop hands them over: each knows its group and its bounds (poster art spans the canvas, a logo does not).
  const wire = (doc) => { const walk = (layers, parent) => layers.forEach((L) => { L.parent = parent; if (!L.bounds) L.bounds = parent && /^logo$/i.test(parent.name) ? NARROW : POSTER; if (L.layers) walk(L.layers, L); }); walk(doc.layers, null); return doc; };
  const posterKid = (name) => docA().layers[1].layers.find((L) => L.name === name);
  const onRow = (act) => !document.querySelector('[data-act="' + act + '"]').classList.contains('hidden');
  const row = () => ['poster-export', 'squareart', 'edit', 'save-bg', 'send-back', 'edit-cancel'].filter(onRow).join(' ');
  const activeTab = () => app.documents.find((d) => d.id >= 500 && d === app._active);
  const poll = async () => { await docPoll[0].fn(); await settle(); };
  const startEdit = async (posterA, select) => { await reset(posterA); wire(docA()); if (select) docA().activeLayers = [select()]; click('[data-act="edit"]'); await settle(); };

  // BU. Edit opens the poster on screen in its own tab, and the row follows the active tab
  await reset([lyr('main', true), lyr('s1')]); wire(docA());
  ok(row() === 'poster-export squareart edit', 'BU: a PSD shows Poster, Square Art and Edit (' + row() + ')');
  click('[data-act="edit"]'); await settle();
  ok(edits.length === 1 && edits[0].layer === 'main' && edits[0].name === 'main · Show (2020)', 'BU: the visible poster opens in a tab named after it: ' + JSON.stringify(edits));
  ok(plain().indexOf('Editing "main" in a new tab. Clean it up, then press Send back.') >= 0, 'BU: it says what to do next: ' + plain());
  ok(row() === 'save-bg send-back edit-cancel', 'BU: in a poster\'s edit tab the row is Save BG, Send back and X (' + row() + ')');
  // Two quick presses while Photoshop is still being asked where LOGO is (a template with none at the top level): one tab
  await reset([lyr('main', true), lyr('s1')]); wire(docA());
  const quickFind = TL.findLogoGroup;
  TL.findLogoGroup = async (doc) => { await new Promise((r) => setTimeout(r, 30)); return quickFind(doc); };
  click('[data-act="edit"]'); click('[data-act="edit"]');
  await new Promise((r) => setTimeout(r, 120)); await settle();
  TL.findLogoGroup = quickFind;
  ok(edits.length === 1, 'BU: Edit pressed twice while the logo lookup is still out opens one tab, not two (' + edits.length + ')');
  ok(plain().some((t) => /^Edit: busy/.test(t)), 'BU: the second press is told it is busy: ' + plain());
  await reset([lyr('main', true), lyr('s1')]); wire(docA());
  click('[data-act="edit"]'); await settle();
  const tabBU = activeTab();
  app.activeDocument = docA(); await poll();
  ok(row() === 'poster-export squareart edit', 'BU: back on the PSD the row is its own again (' + row() + ')');
  app.activeDocument = tabBU; await poll();
  ok(row() === 'save-bg send-back edit-cancel', 'BU: and on the tab again it offers Send back (' + row() + ')');
  click('[data-act="edit"]'); await settle();
  ok(count('openLayerInTab') === 1, 'BU: an edit tab is never opened from an edit tab');

  // BV. which layer Edit opens
  await startEdit([lyr('main', true)], () => docA().layers[0].layers[0]);
  ok(edits[0].layer === 'logo art' && plain().some((t) => /^Editing the logo "logo art"/.test(t)), 'BV: a logo selected in LOGO wins over the poster: ' + JSON.stringify(edits));
  await startEdit([lyr('main', true), Object.assign(lyr('pasted logo', true), { bounds: NARROW })], () => posterKid('pasted logo'));
  ok(edits[0].layer === 'pasted logo' && plain().some((t) => /^Editing the logo/.test(t)), 'BV: so does a selected untagged POSTER layer narrower than a poster: ' + JSON.stringify(edits));
  await startEdit([lyr('Layer 1', true)]);
  ok(edits[0].layer === 'Layer 1' && plain().some((t) => /^Editing "Layer 1"/.test(t)), 'BV: an untagged full-width layer (a pasted backdrop) is a poster: ' + JSON.stringify(edits));
  await startEdit([lyr('main', true), lyr('s1', true), lyr('s2', true)], () => posterKid('s2'));
  ok(edits[0].layer === 's2', 'BV: with several posters visible, the selected one opens: ' + JSON.stringify(edits));
  await startEdit([lyr('main', true), lyr('s1', true)]);
  ok(edits[0].layer === 'main', 'BV: with none selected, the top one opens: ' + JSON.stringify(edits));
  await startEdit([lyr('main'), lyr('s3')], () => posterKid('s3'));
  ok(edits[0].layer === 's3', 'BV: with none visible, a selected tagged layer opens even though it is hidden: ' + JSON.stringify(edits));
  await startEdit([lyr('main'), lyr('s3')]);
  ok(edits.length === 0 && plain().indexOf('Edit: no visible poster in POSTER to edit, and no logo selected.') >= 0, 'BV: nothing to open says so: ' + plain());
  await startEdit([Object.assign(lyr('small scrap', true), { bounds: NARROW })]);
  ok(edits.length === 0, 'BV: an unselected narrow untagged layer is not taken for the poster');

  // BW. Send back: what the note says for each way it can land
  const sendBack = async (answer, posterA, select) => { await startEdit(posterA || [lyr('main', true)], select); if (answer) outcome.sendBackFromTab = answer; click('[data-act="send-back"]'); await settle(); };
  await sendBack(null);
  ok(count('sendBackFromTab') === 1 && plain().indexOf('Sent "main" back to the same spot.') >= 0 && row() === 'poster-export squareart edit', 'BW: same size: ' + plain() + ' / ' + row());
  await sendBack({ ok: true, mode: 'poster', refit: true });
  ok(plain().indexOf('Sent "main" back. Its size changed, so it was fitted like Fit Poster.') >= 0, 'BW: a resized poster: ' + plain());
  await sendBack({ ok: true, mode: 'logo', refit: false }, [lyr('main', true)], () => docA().layers[0].layers[0]);
  ok(plain().indexOf('Sent the logo "logo art" back to the same spot.') >= 0, 'BW: a logo: ' + plain());
  await sendBack({ ok: true, mode: 'logo', refit: true }, [lyr('main', true)], () => docA().layers[0].layers[0]);
  ok(plain().indexOf('Sent the logo "logo art" back. Its size changed, so it was placed like Place Logo.') >= 0, 'BW: a resized logo: ' + plain());
  await sendBack({ ok: true, mode: 'newLogo', share: 0.874, hidOld: 1 });
  ok(plain().indexOf('Sent back as a logo: "Show (2020) - Logo" in LOGO, placed like Place Logo (87% see-through). The earlier logo was hidden. The poster is unchanged.') >= 0, 'BW: a poster cut down to its lettering: ' + plain());
  ok(calls.find((c) => c.fn === 'sendBackFromTab') && alerts().length === 0, 'BW: none of these is a red note');

  // BX. Send back that cannot land: red, and the tab stays an edit tab
  for (const [reason, why] of [['psd-closed', 'the PSD "Show (2020)" is closed'], ['no-logo-group', 'this looks like a logo (it is see-through), but the PSD has no LOGO group'], ['layer-gone', 'the layer "main" is no longer in the PSD']]) {
    await sendBack({ ok: false, reason });
    ok(alerts().join() === 'Send back "main": ' + why + '. The tab stays open, so nothing is lost.' && row() === 'save-bg send-back edit-cancel', 'BX: ' + reason + ': ' + alerts() + ' / ' + row());
  }
  await sendBack(new Error('the cleaned layer did not arrive in the PSD'));
  ok(alerts().join() === 'Send back "main": the cleaned layer did not arrive in the PSD. The tab stays open, so nothing is lost.' && row() === 'save-bg send-back edit-cancel', 'BX: a failure part-way: ' + alerts());
  delete outcome.sendBackFromTab;
  click('[data-act="send-back"]'); await settle();
  ok(count('sendBackFromTab') === 2 && plain().indexOf('Sent "main" back to the same spot.') >= 0, 'BX: and Send back can simply be pressed again');
  await reset([lyr('main', true)]); wire(docA());
  click('[data-act="send-back"]'); await settle();
  ok(count('sendBackFromTab') === 0 && plain().some((t) => /^Send back: switch to an edit tab first/.test(t)), 'BX: Send back outside an edit tab does nothing');

  // BY. X closes the tab on the second press only
  await startEdit([lyr('main', true)]);
  click('[data-act="edit-cancel"]'); await settle();
  ok(count('closeEditTab') === 0 && asks().join() === 'Press ✕ again to close this edit tab without sending it back.', 'BY: the first press asks: ' + asks());
  clockAhead += 11000;
  click('[data-act="edit-cancel"]'); await settle();
  ok(count('closeEditTab') === 0 && asks().length === 1, 'BY: after 10 s a press asks again');
  click('[data-act="edit-cancel"]'); await settle();
  ok(count('closeEditTab') === 1 && count('sendBackFromTab') === 0 && plain().indexOf('Closed the edit tab for "main". The PSD is unchanged.') >= 0 && row() === 'poster-export squareart edit', 'BY: the second press closes it and nothing is sent back: ' + plain());

  // BZ. a tab closed by hand is forgotten, and Square Art takes Edit off the row while it crops
  await startEdit([lyr('main', true)]);
  closeTab(activeTab(), 1); await poll();
  ok(row() === 'poster-export squareart edit', 'BZ: closing the tab in Photoshop gives the row back (' + row() + ')');
  click('[data-act="edit"]'); await settle();
  ok(count('openLayerInTab') === 2, 'BZ: and Edit works again on that PSD');
  await reset([lyr('main', true)]); wire(docA());
  outcome.makeCropDoc = { ok: true, doc: { id: 78 }, width: 950, height: 1425 };
  click('[data-act="squareart"]'); await settle();
  ok(!onRow('edit') && !onRow('send-back'), 'BZ: while Square Art crops, Edit is off the row');
  click('[data-act="edit"]'); await settle();
  ok(count('openLayerInTab') === 0, 'BZ: and Edit does nothing if pressed anyway');
  click('[data-act="sq-cancel"]'); await settle();
  ok(row() === 'poster-export squareart edit', 'BZ: after the crop Edit is back (' + row() + ')');


  // ---- the gear's backdrop step: complete waits until the title's background is saved ----
  const BG = 'Show (2020) {tmdb-1} - background.jpg';
  const waitNotes = () => Array.from(document.querySelectorAll('.waits .waitnote')).map((d) => d.querySelector('.waittext').firstChild.textContent);
  const waitStatus = () => Array.from(document.querySelectorAll('.waits .waitstatus')).map((d) => d.textContent).join(' | ');
  const savedWaits = () => JSON.parse(window.localStorage.getItem('posterflow.backdropWaits') || '[]');
  const tick = async () => { await waitPoll.fn(); await settle(); };
  const finishWith = async (claim, opts = {}) => {
    await reset([lyr('poster art', true)]);
    remote.on = opts.link !== false; server.claim = claim; server.background = !!opts.saved;
    if (opts.fail) Object.assign(server.fail, opts.fail);
    rightClick(chipByText('MOVIE')); await settle();
  };
  ok(!stepChip('movie', 'backdrop').classList.contains('on') && !stepChip('show', 'backdrop').classList.contains('on'), 'CA: the backdrop step starts switched off');
  await stepsOn('movie', { upload: true, backdrop: true, complete: true });

  // CA. the background is already saved: the request is completed as usual
  await finishWith(MATCH, { saved: true });
  ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1} | PUT requests/<id>/posters/Show (2020) {tmdb-1}.jpg | POST requests/<id>/posters | GET background ' + BG + ' | POST requests/<id>/complete', 'CA: upload, ask about the background, complete: ' + sent());
  ok(/✓ upload \(posted 1 poster to The Spy \(2019\)\)  ·  ✓ backdrop \(already saved\)  ·  ✓ complete/.test(communityNote()) && waitNotes().length === 0, 'CA: ' + communityNote());

  // CB. not saved yet: the upload goes out, the complete waits, and the wait is kept
  await finishWith(MATCH);
  ok(!/complete$/.test(sent()) && /✓ backdrop \(not saved yet\)  ·  complete waits for the backdrop$/.test(communityNote()), 'CB: nothing is completed: ' + communityNote());
  ok(waitNotes().join() === 'Waiting for backdrop: The Spy (2019)' && document.querySelectorAll('[data-wait-now]').length === 1 && document.querySelectorAll('[data-wait-stop]').length === 1, 'CB: an amber note with Complete now and X: ' + waitNotes());
  const kept = savedWaits();
  ok(kept.length === 1 && kept[0].id === ID && kept[0].where === 'requests' && kept[0].file === BG && kept[0].kind === 'movie', 'CB: saved, so it outlasts a restart: ' + JSON.stringify(kept));
  server.calls = [];
  await tick();
  ok(sent() === 'GET background ' + BG && /^checked .* not saved yet$/.test(waitStatus()), 'CB: the check asks Posterflow, and the status line says what it found: ' + sent() + ' / ' + waitStatus());
  // the background is saved: the request is looked up again and completed
  server.background = true; server.calls = [];
  await tick();
  ok(sent() === 'GET background ' + BG + ' | GET claim?kind=movie&name=Show (2020) {tmdb-1} | POST requests/<id>/complete', 'CB: once it is saved, the request is completed: ' + sent());
  ok(waitNotes().length === 0 && savedWaits().length === 0 && plain().indexOf('Completed The Spy (2019) (backdrop saved).') >= 0, 'CB: and the wait is gone: ' + plain());

  // CC. Complete now, and X
  await finishWith(MATCH); server.calls = [];
  click('[data-wait-now]'); await settle();
  ok(sent() === 'GET claim?kind=movie&name=Show (2020) {tmdb-1} | POST requests/<id>/complete' && plain().indexOf('Completed The Spy (2019) (completed without a backdrop).') >= 0 && waitNotes().length === 0, 'CC: Complete now completes without a backdrop: ' + sent());
  await finishWith(MATCH); server.calls = [];
  click('[data-wait-stop]'); await settle();
  ok(server.calls.length === 0 && waitNotes().length === 0 && savedWaits().length === 0 && plain().indexOf('Stopped waiting for the backdrop: The Spy (2019). The request is still open.') >= 0, 'CC: X stops waiting and completes nothing: ' + plain());

  // CD. a request completed or released elsewhere just clears
  for (const answer of [{ status: 'none' }, { status: 'done', label: 'The Spy (2019)' }, Object.assign({}, MATCH, { id: 'another-request' })]) {
    await finishWith(MATCH);
    server.background = true; server.claim = answer; server.calls = [];
    await tick();
    ok(!/complete$/.test(sent()) && waitNotes().length === 0 && plain().indexOf('Backdrop wait cleared: The Spy (2019) is no longer open.') >= 0 && alerts().length === 0, 'CD: ' + answer.status + ': cleared without completing anything (' + sent() + ')');
  }

  // CE. a complete that fails keeps waiting, says so once, and is tried again
  await finishWith(MATCH);
  server.background = true; server.fail.complete = 'Could not reach the community service.';
  await tick(); await tick();
  ok(waitNotes().length === 1 && /complete failed: Could not reach the community service · retrying$/.test(waitStatus()), 'CE: the status line carries the reason: ' + waitStatus());
  ok(alerts().join() === 'Could not complete The Spy (2019): Could not reach the community service. Still waiting. It retries.', 'CE: one red note, not one per check: ' + alerts());
  delete server.fail.complete;
  await tick();
  ok(waitNotes().length === 0 && plain().indexOf('Completed The Spy (2019) (backdrop saved).') >= 0, 'CE: the next check completes it');

  // CF. no wait sits here forever: after 3 days it goes, in red, and the request is left open
  await finishWith(MATCH); server.calls = [];
  clockAhead += 3 * 86400000 - 120000;
  await tick();
  ok(waitNotes().length === 1, 'CF: just under 3 days it is still waiting');
  clockAhead += 240000; server.calls = [];
  await tick();
  ok(waitNotes().length === 0 && savedWaits().length === 0 && server.calls.length === 0, 'CF: past 3 days the wait is dropped and nothing is completed (' + sent() + ')');
  ok(alerts().join() === 'Stopped waiting for the backdrop after 3 days: The Spy (2019). The request is still open.', 'CF: it says so in red: ' + alerts());

  // CG. nothing to complete: the note is only a reminder
  await finishWith({ status: 'none' });
  ok(waitNotes().join() === 'Backdrop not saved yet: Show (2020)' && document.querySelectorAll('[data-wait-now]').length === 0 && /backdrop \(not saved yet, reminder added\)/.test(communityNote()), 'CG: no request: a reminder with no Complete now: ' + waitNotes() + ' / ' + communityNote());
  server.background = true; server.calls = [];
  await tick();
  ok(sent() === 'GET background ' + BG && waitNotes().length === 0 && plain().indexOf('Backdrop saved: Show (2020).') >= 0, 'CG: it clears when the background is saved, and completes nothing: ' + sent());
  await finishWith({ status: 'none' });
  clockAhead += 3 * 86400000 + 60000;
  await tick();
  ok(waitNotes().length === 0 && alerts().length === 0 && plain().indexOf('Backdrop reminder dropped after 3 days: Show (2020).') >= 0, 'CG: a reminder runs out quietly: ' + plain());

  // CH. what stops the step: a failed upload, a server with no background folder, no link
  await finishWith(MATCH, { fail: { post: 'Discord post failed at message 1 of 1: boom' } });
  ok(!/background/.test(sent()) && waitNotes().length === 0 && alerts().some((t) => /backdrop and complete skipped/.test(t)), 'CH: a failed upload: the backdrop is not even asked about: ' + alerts());
  await finishWith(MATCH, { fail: { background: 'No background export folder configured.' } });
  ok(!/complete$/.test(sent()) && waitNotes().length === 0 && alerts().some((t) => /✗ backdrop: No background export folder configured\.  ·  complete skipped/.test(t)), 'CH: Posterflow has no background folder: red, nothing completed: ' + alerts());
  await finishWith(MATCH, { link: false });
  ok(server.calls.length === 0 && /backdrop skipped \(no Posterflow link/.test(communityNote()), 'CH: no link: ' + communityNote());
  // a wait with the link switched off says why it cannot check
  await finishWith(MATCH);
  remote.on = false; server.calls = [];
  await tick();
  ok(server.calls.length === 0 && /^no Posterflow link/.test(waitStatus()), 'CH: a wait without the link: ' + waitStatus());
  await stepsOn('movie', { upload: false, backdrop: false, complete: false });

  // ---- Save BG: a cleaned backdrop becomes the title's background ----
  const saveBg = async (setup) => { await startEdit([lyr('Layer 1', true)]); await linkOn(true); if (setup) setup(); click('[data-act="save-bg"]'); await settle(); };
  await startEdit([lyr('main', true)], () => docA().layers[0].layers[0]);
  ok(row() === 'send-back edit-cancel', 'CI: a logo\'s edit tab has no Save BG (' + row() + ')');

  // CI. with the Posterflow link the background goes to Posterflow, then the tab closes and the layer leaves the PSD
  await saveBg();
  ok(server.puts.join() === 'background-exports/' + BG && count('renderBytes') === 1 && count('writeViaTemp') === 0, 'CI: sent to Posterflow under the title\'s background name: ' + server.puts);
  ok(count('closeEditTabAndRemove') === 1 && row() === 'poster-export squareart edit' && plain().some((t) => /^Saved "Show \(2020\) \{tmdb-1\} - background\.jpg" \(1200 x 1500\) in Posterflow and closed the edit tab\. "Layer 1" was removed from the PSD\. Undo brings it back\.$/.test(t)), 'CI: ' + plain());

  // CJ. one already saved is replaced only on a second press
  await saveBg(() => { server.background = true; });
  ok(server.puts.length === 0 && asks().join() === 'A background for "Show (2020)" is already in Posterflow. Press Save BG again to replace it.' && row() === 'save-bg send-back edit-cancel', 'CJ: the first press asks: ' + asks());
  click('[data-act="save-bg"]'); await settle();
  ok(server.puts.length === 1 && plain().some((t) => /^Replaced "/.test(t)), 'CJ: the second press replaces it: ' + plain());

  // CK. without the link, or when Posterflow has no background folder, it goes to the maker's own backgrounds folder
  await startEdit([lyr('Layer 1', true)]);
  click('[data-act="save-bg"]'); await settle();
  ok(server.calls.length === 0 && saves.length === 1 && saves[0].folder === 'BACKGROUNDS' && saves[0].filename === BG && plain().some((t) => / in BACKGROUNDS and closed the edit tab/.test(t)), 'CK: no link: ' + JSON.stringify(saves));
  await saveBg(() => { server.fail.background = 'No background export folder configured.'; });
  ok(server.puts.length === 0 && saves.length === 1, 'CK: Posterflow has no background folder: the maker\'s own is used: ' + JSON.stringify(saves));
  await startEdit([lyr('Layer 1', true)]); local.files = [BG];
  click('[data-act="save-bg"]'); await settle();
  ok(saves.length === 0 && asks().join() === 'A background for "Show (2020)" is already in BACKGROUNDS. Press Save BG again to replace it.', 'CK: one already in that folder is asked about too: ' + asks());
  await startEdit([lyr('Layer 1', true)]); local.picked = false;
  click('[data-act="save-bg"]'); await settle();
  ok(saves.length === 0 && plain().indexOf('Save BG cancelled: no backgrounds folder chosen.') >= 0 && row() === 'save-bg send-back edit-cancel', 'CK: no folder chosen: nothing saved, the tab stays');

  // CL. what cannot be saved leaves the tab open
  await saveBg(() => { server.fail.put = 'upload failed (HTTP 500)'; });
  ok(alerts().join() === 'Save BG "Layer 1": upload failed (HTTP 500). The tab stays open, so nothing is lost.' && count('closeEditTabAndRemove') === 0 && row() === 'save-bg send-back edit-cancel', 'CL: a failed save: ' + alerts());
  await startEdit([lyr('Layer 1', true)]);
  const tabCL = activeTab();
  app.documents = app.documents.filter((d) => d.id !== 1); app.activeDocument = tabCL;
  click('[data-act="save-bg"]'); await settle();
  ok(alerts().join() === 'Save BG: the PSD "Show (2020)" is closed. The tab stays open, so nothing is lost.' && saves.length === 0, 'CL: the PSD was closed: ' + alerts());


  // ---- Edit in tab and Send back as Plugins > Posterflow menu commands ----
  ok(menu && Object.keys(menu.commands).sort().join() === 'editInTab,sendBack', 'CN: the panel registers both menu commands: ' + (menu && Object.keys(menu.commands)));
  await reset([lyr('main', true)]); wire(docA());
  await menu.commands.editInTab.run(); await settle();
  ok(count('openLayerInTab') === 1 && edits[0].layer === 'main' && row() === 'save-bg send-back edit-cancel', 'CN: the Edit in tab command does what the Edit button does (' + row() + ')');
  await menu.commands.sendBack.run(); await settle();
  ok(count('sendBackFromTab') === 1 && plain().indexOf('Sent "main" back to the same spot.') >= 0 && row() === 'poster-export squareart edit', 'CN: the Send back command does what the Send back button does');
  await menu.commands.sendBack.run(); await settle();
  ok(count('sendBackFromTab') === 1 && plain().some((t) => /^Send back: switch to an edit tab first/.test(t)), 'CN: Send back from the menu outside an edit tab only says so');

  // ---- the folder ask: a bar names the folder before its picker opens ----
  const askBar = document.querySelector('.askbar');
  const askText = () => document.querySelector('.askmsg').textContent + ' | ' + document.querySelector('.askhint').textContent;
  await reset([lyr('main', true)]);
  ok(typeof askFolder === 'function' && askBar.classList.contains('hidden'), 'CM: the panel gives fs.js a way to ask, and the bar starts hidden');
  let answered = [];
  askFolder({ name: 'logo folder', hint: 'where logo PNGs go' }).then((yes) => answered.push('logo ' + yes));
  askFolder({ name: 'Square Art folder', hint: 'where Square Art crops go' }).then((yes) => answered.push('square ' + yes));
  await settle();
  ok(!askBar.classList.contains('hidden') && askText() === 'Choose your logo folder | where logo PNGs go' && answered.length === 0, 'CM: the bar names the first folder and waits: ' + askText());
  click('[data-act="ask-choose"]'); await settle();
  ok(answered.join() === 'logo true' && !askBar.classList.contains('hidden') && askText() === 'Choose your Square Art folder | where Square Art crops go', 'CM: Choose answers it, and the second ask takes the bar: ' + answered + ' / ' + askText());
  click('[data-act="ask-cancel"]'); await settle();
  ok(answered.join() === 'logo true,square false' && askBar.classList.contains('hidden'), 'CM: Cancel answers no and the bar goes: ' + answered);
  click('[data-act="ask-choose"]'); await settle();
  ok(answered.length === 2, 'CM: a press with nothing asked does nothing');

  console.log((fail ? 'FAIL' : 'PASS') + ' — ' + pass + ' checks passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log('CRASH', e); process.exit(1); });
