// Finish-pipeline harness for BOTH Photopea panels: loads a panel in jsdom with a fake Photopea that answers
// every posted script with the echoes and bytes the editor would send, right-clicks MOVIE / SHOW, and checks
// the checklist notes. No Photopea needed. Run from the repo root:
//   NODE_PATH=frontend/node_modules node photopea-posterflow/test/finish-harness.js frontend/public/photopea-plugin.html PFL
//   NODE_PATH=frontend/node_modules node photopea-posterflow/test/finish-harness.js photopea-posterflow/photopea-posterflow.html SZ
const { JSDOM } = require('jsdom');
const path = require('path');
const [,, file, P] = process.argv;
const SZ = P === 'SZ';
const url = SZ ? 'http://localhost/photopea-posterflow.html' : 'http://localhost/photopea-plugin.html?save=&name=&style=';
const SRC = 'http://localhost/api/maker-tools/psd-exports/Show%20(2020)%20%7Btmdb-1%7D.psd?token=x&style=CL2K';
const NAME = 'Show (2020) {tmdb-1}';

function run(caseName, opts) {
  return new Promise(async (resolve) => {
    const scripts = [], log = [];
    let dom;
    const post = (msg) => setTimeout(() => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data: msg })), 0);
    const bytes = (head) => { const b = new ArrayBuffer(64); const u = new Uint8Array(b); for (let i = 0; i < head.length; i++) u[i] = head.charCodeAt(i); return b; };
    const model = { singles: [{ lab: 'SP', n: 'Specials', p: [1, 3], v: false }, { lab: 'C', n: 'Collection', p: [1, 4], v: false }],
      seasons: [{ n: 'SEASONS', p: [2], g: true, v: false, r: 'main' }, { n: '1990-1999', p: [2, 0], g: true, v: false, r: 'decade' },
                { n: 'Season 1', p: [2, 0, 0], g: false, v: false, r: 'season' }, { n: 'Season 2', p: [2, 0, 1], g: false, v: false, r: 'season' }], sequels: [], seqGroup: null };
    const scan = { variants: [{ nm: 'show', p: [3, 0], v: true }, { nm: 's1', p: [3, 1], v: false }, { nm: 's2', p: [3, 2], v: false }],
      seasonText: [{ n: 1, p: [1, 0] }, { n: 2, p: [1, 1] }, { n: 3, p: [1, 2] }], specialsText: [{ p: [1, 3] }], clsText: [], collectionText: [{ p: [1, 4] }],
      poster: opts.untagged ? { count: 4, untagged: ['Layer 2'] } : { count: 3, untagged: [] }, source: SRC, name: NAME };
    const ctxEchoes = () => { if (SZ) post('SZNAME:' + NAME); else { post('PFLURL:' + SRC); post('PFLNAME:' + NAME); } };
    const onScript = (s) => {
      scripts.push(s);
      if (s.includes('"' + P + 'FIN:"')) { ctxEchoes(); post(P + 'FIN:' + JSON.stringify({ poster: true, logo: true })); }
      else if (s.includes('"' + P + 'RENV:"')) { const m = s.match(/t=("(?:[^"\\]|\\.)*")/); post(P + 'RENV:' + JSON.stringify({ from: 'old', to: JSON.parse(m[1]), same: false })); }
      else if (s.includes("'" + P + "TRIM:ok'")) post(P + 'TRIM:ok');
      else if (s.includes('"' + P + 'SAVED:')) post(P + 'SAVED:ok');
      else if (s.includes('BATCHINIT:') || s.includes('SZBINIT:')) post((SZ ? 'SZBINIT:' : 'BATCHINIT:') + JSON.stringify(scan));
      else if (s.includes('"' + P + 'GRAD:"')) post(P + 'GRAD:0');
      else if (s.includes("'" + P + "VIS:ok'")) post(P + 'VIS:ok');
      else if (s.includes('"' + P + 'ACT:"')) post(P + 'ACT:sig');
      else if (s.includes("'" + P + ":'+JSON.stringify")) { if (!SZ) { post('PFLNAME:' + NAME); post('PFLCTX:'); } else post('SZNAME:' + NAME); post(P + 'DET:CL2K'); post(P + 'SIG:sig'); post(P + ':' + JSON.stringify(model)); }
      else if (s.includes('d.saveToOE("psd")')) { ctxEchoes(); post(bytes('8BPS')); }
      else if (s.includes('dd.saveToOE("jpg:0.90")')) post(bytes('JPGB'));
      else if (s.includes('saveToOE("jpg:0.90")')) { ctxEchoes(); post(bytes('JPGB')); }
      else if (s.includes('"' + P + 'SQHOME:"')) log.push('square-art-started');
      else if (s.includes('"' + P + 'LOGOVIS:"')) { ctxEchoes(); post(P + 'LOGOVIS:[]'); post(bytes('PNGB')); }
      else log.push('unhandled script: ' + s.slice(0, 80));
    };
    dom = await JSDOM.fromFile(path.resolve(file), { url, runScripts: 'dangerously', pretendToBeVisual: true,
      beforeParse(window) {
        window.postMessage = (msg) => onScript(msg);
        window.fetch = async () => ({ ok: true, status: 200, json: async () => ({ folder: '/exports' }) });
        window.URL.createObjectURL = () => 'blob:x'; window.URL.revokeObjectURL = () => {};
        window.HTMLAnchorElement.prototype.click = () => {};
        try { window.localStorage.setItem('posterflow.finishSteps', JSON.stringify(opts.steps || {})); } catch (_) {}
      } });
    const w = dom.window;
    const notes = () => Array.from(w.document.querySelectorAll('.notes .msg')).map((n) => n.textContent);
    const chips = () => Array.from(w.document.querySelectorAll('.singles .chip'));
    await new Promise((r) => setTimeout(r, 150));   // boot: FETCH round trip
    const chip = chips().find((c) => c.textContent === opts.chip);
    if (!chip) { resolve({ caseName, ok: false, why: 'chip not rendered; notes=' + JSON.stringify(notes()) }); return; }
    chip.dispatchEvent(new w.MouseEvent('contextmenu', { bubbles: true, cancelable: true, shiftKey: !!opts.shift }));
    if (opts.runSeasons) {
      await new Promise((r) => setTimeout(r, 400));
      const inp = w.document.querySelector('.batchinput'); inp.value = opts.runSeasons;
      w.document.querySelector('[data-act="batch-run"]').click();
    }
    await new Promise((r) => setTimeout(r, 4000));   // downloads are paced 500 ms apart on the standalone
    const all = notes();
    const final = all.find((n) => opts.expect.every((e) => n.includes(e)));
    resolve({ caseName, ok: !!final && (!opts.expectLog || log.includes(opts.expectLog)), notes: all.slice(0, 4), log, unhandled: log.filter((l) => l.startsWith('unhandled')) });
  });
}

(async () => {
  const saveOn = SZ ? { show: { save: true, saveAgain: true }, movie: { save: true } } : {};
  const cases = [
    { caseName: 'MOVIE right-click', chip: 'MOVIE', steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false, trim: true }, saveOn.movie || {}) }), expect: ['MOVIE: ', '✓ cleared', '✓ main', '✓ logo name', '✓ trim', '✓ saved', '✓ JPG', 'opening Square Art'], expectLog: 'square-art-started' },
    { caseName: 'SHOW orange, clean tags', chip: 'SHOW', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (orange', '✓ cleared', '✓ logo name', '✓ saved', '✓ tag batch (3 JPGs)', '✓ show only', '✓ saved again', 'opening Square Art'] },
    { caseName: 'SHOW orange, untagged layer', chip: 'SHOW', untagged: true, steps: saveOn, expect: ['STOPPED. Fix these tags', '"Layer 2" is not tagged'] },
    { caseName: 'SHOW blue, Run 2 seasons', chip: 'SHOW', shift: true, runSeasons: '2', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (blue', '✓ season batch (2 JPGs)', '✓ show only', 'opening Square Art'] },
    { caseName: 'movie with save off', chip: 'MOVIE', steps: { movie: { save: false, logoPng: false, squareArt: false } }, expect: ['MOVIE: ', '✓ cleared', '✓ JPG', 'done'] },
  ];
  let fails = 0;
  for (const c of cases) {
    const r = await run(c.caseName, c);
    console.log((r.ok ? 'PASS ' : 'FAIL ') + r.caseName);
    if (!r.ok) { fails++; console.log('   notes:', JSON.stringify(r.notes, null, 1)); console.log('   log:', r.log, r.why || ''); }
    else if (r.unhandled.length) console.log('   (unhandled scripts:', r.unhandled.length + ')');
  }
  process.exit(fails ? 1 : 0);
})();
