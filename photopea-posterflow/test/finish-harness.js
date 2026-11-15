// Finish-pipeline harness for BOTH Photopea panels: loads a panel in jsdom with a fake Photopea that answers
// every posted script with the echoes and bytes the editor would send, right-clicks MOVIE / SHOW, and checks
// the checklist notes (and that a failed or stopped finish reports in red). No Photopea needed. Run from the repo root:
//   NODE_PATH=frontend/node_modules node photopea-posterflow/test/finish-harness.js frontend/public/photopea-plugin.html PFL
//   NODE_PATH=frontend/node_modules node photopea-posterflow/test/finish-harness.js photopea-posterflow/photopea-posterflow.html SZ
// CASE="part of a case name" runs only the matching cases (each takes about five seconds).
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
    const model = { singles: [{ lab: 'SP', n: 'Specials', p: [1, 3], v: false }, { lab: 'C', n: 'Collection', p: [1, 4], v: false }, { lab: 'CLS', n: 'COMPLETE LIMITED SERIES', p: [1, 5], v: false }],
      seasons: [{ n: 'SEASONS', p: [2], g: true, v: false, r: 'main' }, { n: '1990-1999', p: [2, 0], g: true, v: false, r: 'decade' },
                { n: 'Season 1', p: [2, 0, 0], g: false, v: false, r: 'season' }, { n: 'Season 2', p: [2, 0, 1], g: false, v: false, r: 'season' }], sequels: [], seqGroup: null };
    // POSTER's layers, top to bottom; the first is the visible one. Tagged names are what the scan reports.
    const names = opts.layers || ['show', 's1', 's2'].concat(opts.cVariant ? ['c'] : []).concat(opts.untagged ? ['Layer 2'] : []);
    const isTag = (n) => /^(show|main|cls|c|s\d+(-\d+)?)$/.test(n);
    const scan = { variants: names.map((n, i) => ({ nm: n, p: [3, i], v: i === 0 })).filter((v) => isTag(v.nm)),
      seasonText: [{ n: 1, p: [1, 0] }, { n: 2, p: [1, 1] }, { n: 3, p: [1, 2] }], specialsText: [{ p: [1, 3] }], clsText: [{ p: [1, 5] }], collectionText: [{ p: [1, 4] }],
      poster: { count: names.length, untagged: names.filter((n) => !isTag(n)) }, source: SRC, name: NAME };
    const full = [-10, 25, 1010, 1450], short = [-10, 25, 1010, 1300];   // covers the CL2K box / stops 75 px above its guide
    const kids = names.map((n, i) => ({ n, v: i === 0, b: i === opts.short ? short : full }));
    const ctxEchoes = () => { if (SZ) post('SZNAME:' + NAME); else { post('PFLURL:' + SRC); post('PFLNAME:' + NAME); } };
    // The document in front. A script's own "which document is this" function runs against it, so the panel's
    // real rule is what gets tested. opts.switchAfter / renameAfter / closeAfter: once a script holding that
    // text has been answered, another tab comes to the front / Photopea renames this tab / it is closed.
    let active = { name: NAME, source: SRC, width: 1000, height: 1500 };
    const printFn = (s) => {
      const at = s.indexOf('function pfPrint(D){'); if (at < 0) return null;
      let i = s.indexOf('{', at), depth = 0;
      for (; i < s.length; i++) { if (s[i] === '{') depth++; else if (s[i] === '}' && --depth === 0) break; }
      return (0, eval)('(' + s.slice(at, i + 1) + ')');
    };
    const printNow = (f) => { try { return f(active); } catch (_) { return ''; } };
    let moved = false;
    const afterScript = (s) => {
      if (moved) return;
      if (opts.switchAfter && s.includes(opts.switchAfter)) { moved = true; active = { name: 'Other (2021) {tmdb-2}', source: 'local,0,Other (2021) {tmdb-2}.psd', width: 1000, height: 1500 }; log.push('another tab came to the front'); }
      if (opts.renameAfter && s.includes(opts.renameAfter)) { moved = true; active.name = 'Show'; log.push('the tab was renamed'); }
      if (opts.closeAfter && s.includes(opts.closeAfter)) { moved = true; active = null; log.push('the document was closed'); }
    };
    const onScript = (s) => { answer(s); afterScript(s); };
    const answer = (s) => {
      scripts.push(s);
      // measured in the real Photopea: a script that calls a function expression on the spot gets no reply at all
      if (/\(function\s*\(/.test(s)) { log.push('unhandled script: Photopea drops an immediately-invoked function: ' + s.slice(0, 60)); return; }
      const f = printFn(s), own = s.match(/!==("(?:[^"\\]|\\.)*")\)\{/);
      // a script that names its document changes nothing on another one
      if (f && own && printNow(f) !== JSON.parse(own[1])) {
        if (s.includes('"' + P + 'SWITCHED:"')) { log.push('refused: another document'); post(P + 'SWITCHED:'); }
        else if (s.includes('"' + P + 'CLEAN:ok"')) { log.push('cleanup refused: another document'); post(P + 'BWARN:' + (s.match(/BWARN:(Another document[^"]*)"/) || [])[1]); post(P + 'CLEAN:ok'); }
        else log.push('skipped: another document');
      }
      else if (s.includes('"' + P + 'PRINT:"')) post(P + 'PRINT:' + (f ? printNow(f) : ''));
      else if (s.includes("'" + P + "COVER:'")) {
        // Real Photopea writes a bound as {"ne":"UnitValue","d":25,"aIp":"px"} unless the script's nv() multiplies it by 1.
        const plain = s.includes("if(typeof x=='number')return x*1;");
        const asSent = kids.map((k) => Object.assign({}, k, { b: plain ? k.b : k.b.map((d) => ({ ne: 'UnitValue', d, aIp: 'px' })) }));
        post(P + 'COVER:' + JSON.stringify({ name: NAME, poster: true, pi: 3, cw: 1000, kids: asSent }));
      }
      // collection export: the script moves the logo, says by how much, then exports; the cleanup must move it back
      else if (s.includes('"' + P + 'COLMV:"')) {
        log.push('collection-moved'); post(P + 'COLMV:-100,0|[]');
        if (opts.afterMove === 'error') post(P + 'ERR:export exploded');   // a failure Photopea reports
        else if (opts.afterMove !== 'silent') { log.push('jpg-export'); post(bytes('JPGB')); }   // 'silent': the script died, nothing more arrives
      }
      else if (s.includes('"' + P + 'CLEAN:late"')) { log.push('late-marker'); post(P + 'CLEAN:late'); }
      else if (s.includes('"' + P + 'CLEAN:ok"')) { log.push(s.includes('translate(0,100)') ? 'collection-restored' : 'collection-NOT-restored'); post(P + 'CLEAN:ok'); }
      else if (s.includes('"' + P + 'FIN:"')) { ctxEchoes(); post(P + 'FIN:' + JSON.stringify(Object.assign({ poster: true, logo: !opts.noLogo }, f ? { print: printNow(f) } : {}))); }
      else if (s.includes('"' + P + 'RENV:"')) { const m = s.match(/t=("(?:[^"\\]|\\.)*")/); post(P + 'RENV:' + JSON.stringify({ from: 'old', to: JSON.parse(m[1]), same: false })); }
      else if (s.includes("'" + P + "TRIM:ok'")) post(P + 'TRIM:ok');
      else if (s.includes('"' + P + 'SAVED:')) post(P + 'SAVED:ok');
      else if (s.includes('BATCHINIT:') || s.includes('SZBINIT:')) post((SZ ? 'SZBINIT:' : 'BATCHINIT:') + JSON.stringify(Object.assign({}, scan, f ? { print: printNow(f) } : {})));
      else if (s.includes('"' + P + 'GRAD:"')) post(P + 'GRAD:0');
      else if (s.includes("'" + P + "VIS:ok'")) post(P + 'VIS:ok');
      else if (s.includes('"' + P + 'ACT:"')) post(P + 'ACT:sig');
      else if (s.includes("'" + P + ":'+JSON.stringify")) { if (!SZ) { post('PFLNAME:' + NAME); post('PFLCTX:'); } else post('SZNAME:' + NAME); post(P + 'DET:' + (opts.style || 'CL2K')); post(P + 'SIG:sig'); post(P + ':' + JSON.stringify(model)); }
      else if (s.includes('d.saveToOE("psd")')) { ctxEchoes(); post(bytes('8BPS')); }
      else if (s.includes('dd.saveToOE("jpg:0.90")')) { if (opts.silentExport) log.push('export-dropped'); else { log.push('jpg-export'); post(bytes('JPGB')); } }
      else if (s.includes('saveToOE("jpg:0.90")')) { log.push('jpg-export'); ctxEchoes(); post(bytes('JPGB')); }
      else if (s.includes('"' + P + 'SQHOME:"')) log.push('square-art-started');
      else if (s.includes('"' + P + 'LOGOVIS:"')) { ctxEchoes(); post(P + 'LOGOVIS:[]'); post(bytes('PNGB')); }
      else log.push('unhandled script: ' + s.slice(0, 80));
    };
    dom = await JSDOM.fromFile(path.resolve(file), { url, runScripts: 'dangerously', pretendToBeVisual: true,
      beforeParse(window) {
        window.postMessage = (msg) => onScript(msg);
        const st = window.setTimeout.bind(window);   // the panel waits 60 s for a batch item's reply; the harness does not
        window.setTimeout = (fn, ms, ...a) => st(fn, ms === 60000 ? 400 : ms, ...a);
        // Posterflow: image and PSD saves answer with a folder; the community endpoints answer as the case says
        const si = window.setInterval.bind(window);   // the backdrop waits are checked every 3 s; the harness does not wait that long
        window.setInterval = (fn, ms, ...a) => si(fn, ms === 3000 ? 700 : ms, ...a);
        if (opts.seedWaits) { try { window.localStorage.setItem('posterflow.backdropWaits', JSON.stringify(typeof opts.seedWaits === 'function' ? opts.seedWaits() : opts.seedWaits)); } catch (_) {} }
        let backgroundAsks = 0;
        window.fetch = async (url, init) => {
          // is the title's background saved? opts.background: true, or the number of the ask from which it is
          if (String(url).indexOf('/api/maker-tools/artwork-exports/background/') >= 0) {
            log.push('api GET background ' + decodeURIComponent(String(url).split('/').pop()));
            if (opts.backgroundFails) return { ok: false, status: 400, json: async () => ({ detail: opts.backgroundFails }) };
            backgroundAsks++;
            return { ok: true, status: 200, json: async () => ({ exists: opts.background === true || (typeof opts.background === 'number' && backgroundAsks >= opts.background) }) };
          }
          const at = String(url).indexOf('/api/maker-tools/community/');
          if (at < 0) return { ok: true, status: 200, json: async () => ({ folder: '/exports' }) };
          const what = ((init && init.method) || 'GET') + ' ' + decodeURIComponent(String(url).slice(at + 27).replace(/[0-9a-f]{8}-[0-9a-f-]{27}/, '<id>'));
          log.push('api ' + what);
          const fail = (detail) => ({ ok: false, status: 502, json: async () => ({ detail }) });
          const claim = opts.claim || { status: 'none' }, files = opts.files || [];
          const answer = (body) => ({ ok: true, status: 200, json: async () => body });
          // what Posterflow says an upload did: its Upload button's settings decide (opts.idarr = it also adds to IDarr)
          const did = (n) => (n ? 'posted ' + n + ' poster' + (n === 1 ? '' : 's') + ' to ' + claim.label + (opts.idarr ? ' and added ' + (n === 1 ? 'it' : 'them') + ' to IDarr (Posters)' : '') : '');
          const done = (n, more) => answer(Object.assign({ posted: n, messages: n ? 1 : 0, idarr: opts.idarr ? n : 0, drive: 'Posters', warnings: [], did: did(n) }, more));
          if (/ claim\?/.test(what)) return answer(claim);
          // the Upload button's pick: the claim answer, plus what the export folders hold when an upload can run
          if (/ upload\?/.test(what)) return answer(Object.assign({}, claim, claim.status === 'match' && !claim.upload.problem ? { folders: opts.folders === undefined ? 2 : opts.folders, files } : {}));
          if (/^POST .*\/(posters|upload)$/.test(what) && opts.postFails) return fail(opts.postFails);
          if (/^POST .*\/complete$/.test(what) && opts.completeFails) return fail(opts.completeFails);
          if (/^POST .*\/upload$/.test(what)) return done(files.length);
          if (/^PUT /.test(what)) return answer({ staged: 1 });
          if (/^POST .*\/posters$/.test(what)) {   // a finish's staged JPGs: the request asks for all but the names opts.unwanted matches
            const staged = log.filter((l) => /^api PUT/.test(l)), skipped = staged.filter((l) => opts.unwanted && opts.unwanted.test(l)).map((l) => l.replace(/^.*\/posters\//, ''));
            return done(staged.length - skipped.length, { skipped });
          }
          return answer({ ok: true });
        };
        window.URL.createObjectURL = () => 'blob:x'; window.URL.revokeObjectURL = () => {};
        window.HTMLAnchorElement.prototype.click = () => {};
        try { window.localStorage.setItem('posterflow.finishSteps', JSON.stringify(Object.assign({ v: 2 }, opts.steps || {}))); } catch (_) {}   // v2: the panels ignore older records
      } });
    const w = dom.window;
    // What the panel in another Photopea tab does to the shared list: it saves its own copy. `quiet`: no
    // storage event reaches this panel, so only its own reading of the list can notice.
    if (opts.otherTab) setTimeout(() => {
      w.localStorage.setItem('posterflow.backdropWaits', JSON.stringify(opts.otherTab.waits));
      log.push('another tab saved the list');
      if (!opts.otherTab.quiet) w.dispatchEvent(new w.StorageEvent('storage', { key: 'posterflow.backdropWaits' }));
    }, opts.otherTab.after);
    const noteEls = () => Array.from(w.document.querySelectorAll('.notes .msg, .notes .alert .alerttext'));   // plain notes + red failures
    const notes = () => noteEls().map((n) => n.textContent);
    const chips = () => Array.from(w.document.querySelectorAll('.singles .chip'));
    await new Promise((r) => setTimeout(r, 150));   // boot: FETCH round trip
    if (opts.bolt) {
      w.document.querySelector('[data-act="' + opts.bolt + '"]').click();
      await new Promise((r) => setTimeout(r, 400));
      for (let i = 0; i < (opts.runs || 1); i++) { w.document.querySelector('.batchinput').value = opts.typed || ''; w.document.querySelector('[data-act="batch-run"]').click(); await new Promise((r) => setTimeout(r, 900)); }
    } else if (opts.pressJpg) {
      for (let i = 0; i < opts.pressJpg; i++) { w.document.querySelector('[data-act="jpg"]').click(); await new Promise((r) => setTimeout(r, 700)); }
    } else if (opts.press) {   // bottom-row buttons, one after the other
      for (const act of opts.press) { w.document.querySelector('[data-act="' + act + '"]').click(); await new Promise((r) => setTimeout(r, 500)); }
    } else {
      const chip = chips().find((c) => c.textContent === opts.chip);
      if (!chip) { resolve({ caseName, ok: false, why: 'chip not rendered; notes=' + JSON.stringify(notes()) }); return; }
      chip.dispatchEvent(new w.MouseEvent('contextmenu', { bubbles: true, cancelable: true, shiftKey: !!opts.shift }));
    }
    if (opts.clickWait) {   // a waiting note's own buttons, once the finish has reported
      await new Promise((r) => setTimeout(r, 500));
      const b = w.document.querySelector('[data-wait-' + opts.clickWait + ']');
      if (b) b.click(); else log.push('no waiting note to press ' + opts.clickWait + ' on');
    }
    if (opts.runSeasons !== undefined) {
      await new Promise((r) => setTimeout(r, 400));
      const inp = w.document.querySelector('.batchinput'); inp.value = opts.runSeasons;
      w.document.querySelector('[data-act="batch-run"]').click();
    }
    await new Promise((r) => setTimeout(r, 4000));   // downloads are paced 500 ms apart on the standalone
    const all = notes();
    const final = noteEls().find((n) => opts.expect.every((e) => n.textContent.includes(e)));
    const red = !!final && final.classList.contains('alerttext');   // a failed or stopped finish reports in red
    const jpgsOut = log.filter((l) => l === 'jpg-export').length, redLeft = w.document.querySelectorAll('.notes .alert').length;
    const okJpgs = opts.jpgs === undefined || jpgsOut === opts.jpgs, okNoRed = !opts.noRed || redLeft === 0;
    const bar = w.document.querySelector('.batchmsg').textContent, okBar = !opts.bar || opts.bar.every((t) => bar.includes(t));
    if (!okBar) log.push('batch bar said: ' + bar);
    const okQuiet = !opts.forbidLog || !log.some((l) => opts.forbidLog.test(l));
    const asksLeft = w.document.querySelectorAll('.notes .ask').length, okAsks = opts.asks === undefined || asksLeft === opts.asks;
    const waiting = Array.from(w.document.querySelectorAll('.waits .waittext')).map((d) => d.firstChild.textContent);
    const okWaits = opts.waiting === undefined || waiting.join(' | ') === opts.waiting;
    if (!okWaits) log.push('expected waiting notes "' + opts.waiting + '", saw "' + waiting.join(' | ') + '"');
    let stored = '?'; try { stored = JSON.parse(w.localStorage.getItem('posterflow.backdropWaits') || '[]').map((x) => x.id).join(' | '); } catch (_) {}
    const okStored = opts.stored === undefined || stored === opts.stored;
    if (!okStored) log.push('expected the saved list to hold "' + opts.stored + '", it holds "' + stored + '"');
    const statuses = Array.from(w.document.querySelectorAll('.waits .waitstatus')).map((d) => d.textContent);
    const okStatus = !opts.status || statuses.some((x) => x.includes(opts.status));
    if (!okStatus) log.push('expected a waiting note to say "' + opts.status + '", they say ' + JSON.stringify(statuses));
    if (!okAsks) log.push('expected ' + opts.asks + ' blue question(s) left, saw ' + asksLeft);
    if (!okJpgs) log.push('expected ' + opts.jpgs + ' JPG export(s), saw ' + jpgsOut);
    if (!okNoRed) log.push('expected no red note left, saw ' + redLeft);
    resolve({ caseName, ok: !!final && red === !!opts.red && okJpgs && okNoRed && okBar && okQuiet && okAsks && okWaits && okStored && okStatus && [].concat(opts.expectLog || []).every((l) => log.includes(l)), notes: all.slice(0, 4), log, unhandled: log.filter((l) => l.startsWith('unhandled')) });
  });
}

(async () => {
  const saveOn = SZ ? { show: { save: true, saveAgain: true }, movie: { save: true }, collection: { save: true, saveAgain: true } } : {};
  const cases = [
    { caseName: 'MOVIE right-click', chip: 'MOVIE', steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false, trim: true }, saveOn.movie || {}) }), expect: ['MOVIE: ', '✓ cleared', '✓ main', '✓ logo name', '✓ trim', '✓ saved', '✓ coverage', '✓ JPG', 'opening Square Art'], expectLog: 'square-art-started', jpgs: 1 },
    { caseName: 'SHOW orange, clean tags', chip: 'SHOW', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (orange', '✓ cleared', '✓ logo name', '✓ saved', '✓ coverage', '✓ tag batch (3 JPGs)', '✓ show only', '✓ saved again', 'opening Square Art'] },
    { caseName: 'SHOW orange, untagged layer', chip: 'SHOW', untagged: true, steps: saveOn, expect: ['STOPPED. Fix these tags', '"Layer 2" is not tagged'], red: true },
    { caseName: 'SHOW blue, Run 2 seasons', chip: 'SHOW', shift: true, runSeasons: '2', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (blue', '✓ coverage', '✓ season batch (2 JPGs)', '✓ show only', 'opening Square Art'] },
    { caseName: 'movie with save off', chip: 'MOVIE', steps: { movie: { save: false, logoPng: false, squareArt: false } }, expect: ['MOVIE: ', '✓ cleared', '✓ JPG', 'done'] },
    { caseName: 'COLLECTION right-click', chip: 'COLLECTION', cVariant: true, steps: Object.assign({}, saveOn, { collection: Object.assign({ logoPng: false }, saveOn.collection || {}) }), expect: ['COLLECTION: ', '✓ c', '✓ logo name', '✓ trim', '✓ saved', '✓ coverage', '✓ JPG', '✓ saved again', 'opening Square Art'], expectLog: ['square-art-started', 'collection-moved', 'collection-restored'] },
    { caseName: 'COLLECTION, no c layer', chip: 'COLLECTION', steps: Object.assign({}, saveOn, { collection: Object.assign({ logoPng: false }, saveOn.collection || {}) }), expect: ['COLLECTION: ', '✓ saved', '✗ JPG: no POSTER layer tagged "c"', 'save again and Square Art skipped'], red: true },
    { caseName: 'MOVIE, CL2K without LOGO', chip: 'MOVIE', noLogo: true, steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false }, saveOn.movie || {}) }), expect: ['MOVIE: ', '✗ logo name: no LOGO group', 'PSD saved; coverage and JPG and Square Art skipped'], red: true },
    { caseName: 'MOVIE, MM2K without LOGO', chip: 'MOVIE', noLogo: true, style: 'MM2K', steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false }, saveOn.movie || {}) }), expect: ['MOVIE: ', '– logo name skipped (no LOGO group on MM2K)', '✓ JPG', 'opening Square Art'], expectLog: 'square-art-started' },
    // coverage: blank canvas (a layer's bounds stop short of the bottom guide) stops a finish before its exports
    { caseName: 'MOVIE, blank canvas', chip: 'MOVIE', short: 0, steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false }, saveOn.movie || {}) }), expect: ['MOVIE: ', '✓ saved', '✗ coverage: show: blank canvas (~75 px above the bottom guide)', 'JPG and Square Art skipped'], red: true, jpgs: 0 },
    { caseName: 'SHOW orange, blank season layer and an untagged layer', chip: 'SHOW', short: 1, untagged: true, steps: saveOn, expect: ['✗ coverage: s1: blank canvas (~75 px above the bottom guide)', 'STOPPED. Fix these tags', '"Layer 2" is not tagged'], red: true, jpgs: 0 },
    { caseName: 'SHOW blue, blank canvas', chip: 'SHOW', shift: true, short: 0, steps: saveOn, expect: ['SHOW (blue', '✗ coverage: show: blank canvas (~75 px above the bottom guide)', 'batch and'], red: true, jpgs: 0 },
    { caseName: 'COLLECTION, blank c layer', chip: 'COLLECTION', cVariant: true, short: 3, steps: Object.assign({}, saveOn, { collection: Object.assign({ logoPng: false }, saveOn.collection || {}) }), expect: ['COLLECTION: ', '✗ coverage: c: blank canvas (~75 px above the bottom guide)'], red: true, jpgs: 0 },
    { caseName: 'MOVIE, blank canvas, coverage switched off', chip: 'MOVIE', short: 0, steps: Object.assign({}, saveOn, { movie: Object.assign({ coverage: false, logoPng: false, squareArt: false }, saveOn.movie || {}) }), expect: ['MOVIE: ', '✓ JPG', 'done'], jpgs: 1 },
    { caseName: 'MOVIE, unknown template', chip: 'MOVIE', short: 0, style: 'OTHER', steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false, squareArt: false }, saveOn.movie || {}) }), expect: ['MOVIE: ', '– coverage skipped (unknown template)', '✓ JPG'], jpgs: 1 },
    // the JPG button: blank canvas holds the export back until JPG is pressed again
    { caseName: 'JPG button, clean poster', pressJpg: 1, expect: ['Show (2020) {tmdb-1}.jpg'], jpgs: 1, noRed: true },
    { caseName: 'JPG button, blank canvas, one press', pressJpg: 1, short: 0, expect: ['JPG not exported. show: blank canvas (~75 px above the bottom guide). Press JPG again to export anyway.'], red: true, jpgs: 0 },
    { caseName: 'JPG button, blank canvas, pressed twice', pressJpg: 2, short: 0, expect: ['Show (2020) {tmdb-1}.jpg'], jpgs: 1, noRed: true },
    { caseName: 'JPG button, unknown template', pressJpg: 1, short: 0, style: 'OTHER', expect: ['Show (2020) {tmdb-1}.jpg'], jpgs: 1, noRed: true },
    // a manual ⚡ Run: blank canvas holds the batch back until Run is pressed again
    { caseName: '⚡ orange Run, clean', bolt: 'batch', expect: [], jpgs: 3, noRed: true },
    { caseName: '⚡ orange Run, blank season layer, one press', bolt: 'batch', short: 1, expect: ['Batch not started. s1: blank canvas (~75 px above the bottom guide). Press Run again to export anyway.'], red: true, jpgs: 0 },
    { caseName: '⚡ orange Run, blank season layer, pressed twice', bolt: 'batch', short: 1, runs: 2, expect: [], jpgs: 3, noRed: true },
    { caseName: '⚡ orange Run filtered to main, blank season layer', bolt: 'batch', short: 1, typed: 'main', expect: [], jpgs: 1, noRed: true },
    { caseName: '⚡ blue Run, blank poster, one press', bolt: 'batch-seasons', short: 0, typed: '2', expect: ['Batch not started. show: blank canvas (~75 px above the bottom guide)'], red: true, jpgs: 0 },
    { caseName: '⚡ blue Run, blank poster, pressed twice', bolt: 'batch-seasons', short: 0, typed: '2', runs: 2, expect: [], jpgs: 2, noRed: true },
    // a batch that stops: a moved collection logo goes back; an item that never answers stops the batch
    { caseName: '⚡ collection export, Photopea reports an error after the logo moved', bolt: 'batch', cVariant: true, typed: 'c', afterMove: 'error', expect: [], jpgs: 0,
      expectLog: ['collection-moved', 'collection-restored'], bar: ['Stopped: export exploded', 'The collection logo was moved back'] },
    { caseName: '⚡ collection export, no reply after the logo moved', bolt: 'batch', cVariant: true, typed: 'c', afterMove: 'silent', expect: [], jpgs: 0,
      expectLog: ['collection-moved', 'collection-restored'], bar: ['Stopped: no reply from Photopea while exporting 1/1', 'The collection logo was moved back'] },
    { caseName: '⚡ orange Run, an item never answers', bolt: 'batch', typed: 's1', silentExport: true, expect: [], jpgs: 0,
      expectLog: ['export-dropped', 'late-marker'], bar: ['Stopped: no reply from Photopea while exporting 1/1'] },
    // the blue ⚡ needs a typed range: blank never means every season the template has
    { caseName: '⚡ blue Run, blank box', bolt: 'batch-seasons', typed: '', expect: [], jpgs: 0, noRed: true, bar: ['Type the seasons first'] },
    { caseName: '⚡ blue Run, unreadable text', bolt: 'batch-seasons', typed: 'abc', expect: [], jpgs: 0, noRed: true, bar: ['Could not read "abc"'] },
    { caseName: '⚡ blue Run, a blank poster and a blank box: asks for the seasons first', bolt: 'batch-seasons', short: 0, typed: '', expect: [], jpgs: 0, noRed: true, bar: ['Type the seasons first'] },
    { caseName: 'SHOW blue, blank Run keeps the finish waiting', chip: 'SHOW', shift: true, runSeasons: '', steps: saveOn, expect: ['SHOW (blue', 'Type the seasons and press Run'], jpgs: 0, bar: ['Type the seasons first'] },
    // another tab comes to the front (or the PSD is closed) mid-finish: nothing more runs
    { caseName: 'MOVIE, another tab comes to the front after the trim', chip: 'MOVIE', switchAfter: "TRIM:ok'", steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false }, saveOn.movie || {}) }),
      expect: ['MOVIE: ', '✓ trim', '✗ saved: another document became active, so the finish stopped', 'STOPPED, PSD not saved'], red: true, jpgs: 0, forbidLog: /square-art-started/ },
    { caseName: 'MOVIE, another tab comes to the front after the JPG: Square Art stays shut', chip: 'MOVIE', switchAfter: 'saveToOE("jpg:0.90")', steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false }, saveOn.movie || {}) }),
      expect: ['MOVIE: ', '✓ JPG', '✗ Square Art: another document became active, so the finish stopped'], red: true, jpgs: 1, forbidLog: /square-art-started/ },
    { caseName: 'MOVIE, the PSD is closed after the trim', chip: 'MOVIE', closeAfter: "TRIM:ok'", steps: Object.assign({}, saveOn, { movie: Object.assign({ logoPng: false }, saveOn.movie || {}) }),
      expect: ['MOVIE: ', '✗ saved: the document was closed, so the finish stopped'], red: true, jpgs: 0 },
    { caseName: 'SHOW orange, another tab comes to the front during the batch', chip: 'SHOW', switchAfter: 'dd.saveToOE("jpg:0.90")', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }),
      expect: ['SHOW (orange', '✗ tag batch: another document became active; the batch stopped after 1 JPG', 'skipped'], red: true, jpgs: 1, expectLog: 'refused: another document' },
    { caseName: 'SHOW blue, Run pressed on another tab keeps the finish waiting', chip: 'SHOW', shift: true, switchAfter: SZ ? 'SZBINIT:' : 'BATCHINIT:', runSeasons: '2', steps: saveOn,
      expect: ['SHOW (blue', 'Type the seasons and press Run'], jpgs: 0, bar: ['This finish is waiting on another PSD'] },
    { caseName: '⚡ orange Run on another tab than the one scanned', bolt: 'batch', switchAfter: SZ ? 'SZBINIT:' : 'BATCHINIT:', expect: [], jpgs: 0, noRed: true,
      expectLog: 'refused: another document', bar: ['Stopped: another document became active; the batch stopped after 0 JPGs'] },
    { caseName: '⚡ collection export, another tab comes to the front before the logo is moved back', bolt: 'batch', cVariant: true, typed: 'c', switchAfter: 'COLMV:"',
      expect: ['Another document became active before the collection logo was moved back'], jpgs: 1, expectLog: ['collection-moved', 'cleanup refused: another document'], forbidLog: /collection-(NOT-)?restored/ },
  ].concat(SZ ? [] : [
    // Photopea re-derives a URL-opened tab's name: that is not another document
    { caseName: 'MOVIE, Photopea renames the tab mid-finish: the finish carries on', chip: 'MOVIE', renameAfter: "TRIM:ok'", steps: { movie: { logoPng: false } },
      expect: ['MOVIE: ', '✓ saved', '✓ JPG', 'opening Square Art'], jpgs: 1, expectLog: ['the tab was renamed', 'square-art-started'] },
  ]).concat([
    // right-click LIMITED SERIES: the main JPG and " - Season 1" with the limited series text
    { caseName: 'LIMITED SERIES, one untagged poster makes both JPGs', chip: 'LIMITED SERIES', layers: ['poster art', 'old idea'], steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['LIMITED SERIES: ', '✓ saved', '✓ coverage', '✓ tag batch (2 JPGs)', 'opening Square Art'], jpgs: 2 },
    { caseName: 'LIMITED SERIES, a lone cls layer makes both JPGs', chip: 'LIMITED SERIES', layers: ['cls'], steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['LIMITED SERIES: ', '✓ tag batch (2 JPGs)'], jpgs: 2 },
    { caseName: 'LIMITED SERIES, show and cls layers export as tagged', chip: 'LIMITED SERIES', layers: ['show', 'cls'], steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['LIMITED SERIES: ', '✓ coverage', '✓ tag batch (2 JPGs)'], jpgs: 2 },
    { caseName: 'LIMITED SERIES, a season layer stops it', chip: 'LIMITED SERIES', layers: ['show', 's1'], steps: saveOn, expect: ['LIMITED SERIES: ', 'STOPPED: a limited series has no season posters, but POSTER has "s1"'], red: true, jpgs: 0 },
    { caseName: 'LIMITED SERIES, blank canvas', chip: 'LIMITED SERIES', layers: ['poster art'], short: 0, steps: saveOn, expect: ['LIMITED SERIES: ', '✗ coverage: poster art: blank canvas (~75 px above the bottom guide)'], red: true, jpgs: 0 },
    // orange SHOW with one poster: it waits for the seasons that poster should also make
    { caseName: 'SHOW orange, one poster: a blank Run makes main and Season 1', chip: 'SHOW', layers: ['show'], runSeasons: '', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (orange', '✓ coverage', '✓ tag batch (2 JPGs)', 'opening Square Art'], jpgs: 2 },
    { caseName: 'SHOW orange, one poster: "2" makes main and two seasons', chip: 'SHOW', layers: ['main'], runSeasons: '2', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (orange', '✓ tag batch (3 JPGs)'], jpgs: 3 },
    { caseName: 'SHOW orange, one poster: "0" makes main only', chip: 'SHOW', layers: ['show'], runSeasons: '0', steps: Object.assign({}, saveOn, { show: Object.assign({ logoPng: false }, saveOn.show || {}) }), expect: ['SHOW (orange', '✓ tag batch (1 JPG)'], jpgs: 1 },
    { caseName: 'SHOW orange, one poster: unreadable text keeps it waiting', chip: 'SHOW', layers: ['show'], runSeasons: 'abc', steps: saveOn, expect: ['SHOW (orange', 'One poster: press Run for the main JPG and Season 1'], jpgs: 0, bar: ['Could not read "abc"'] },
  ]);
  // upload and complete go through Posterflow, so only the in-app panel has them
  const DISCORD = { will: 'post to Discord', aside: '', problem: '' };
  const CLAIM = { status: 'match', type: 'request', where: 'requests', id: '11111111-2222-3333-4444-555555555555', label: 'The Spy (2019)', has_thread: true, upload: DISCORD };
  const ITEM = { status: 'match', type: 'list_item', where: 'list-items', id: CLAIM.id, label: 'The Spy · list', has_thread: false,
    upload: { will: '', aside: '', problem: 'The Spy · list is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow' } };
  const TO_IDARR = { will: 'add to IDarr (Posters)', aside: '', problem: '' };
  const both = (f, extra) => ({ [f]: Object.assign({ upload: true, complete: true, logoPng: false, squareArt: false }, extra || {}) });
  const SPY_WAIT = { id: CLAIM.id, at: Date.now(), where: 'requests', kind: 'movie', name: 'Show (2020) {tmdb-1}', label: 'The Spy (2019)', file: 'Show (2020) {tmdb-1} - background.jpg' };
  const OTHER_WAIT = { id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', at: Date.now(), where: 'requests', kind: 'movie', name: 'Other (2021) {tmdb-2}', label: 'Other (2021)', file: 'Other (2021) {tmdb-2} - background.jpg' };
  const FILES = [{ name: 'Show (2020) {tmdb-1}.jpg', poster: 'main', age: '2m ago' }, { name: 'Show (2020) {tmdb-1} - Season 2.jpg', poster: 'Season 2', age: '3d ago' }];
  if (!SZ) cases.push(
    { caseName: 'upload and complete are off by default: nothing is asked of Posterflow', chip: 'MOVIE', claim: CLAIM, steps: { movie: { logoPng: false, squareArt: false } }, expect: ['MOVIE: ', '✓ JPG', 'done'], forbidLog: /^api / },
    { caseName: 'MOVIE with upload and complete on: look up, send, upload, complete', chip: 'MOVIE', claim: CLAIM, steps: both('movie'),
      expect: ['MOVIE · Show (2020): ✓ upload (posted 1 poster to The Spy (2019))', '✓ complete (The Spy (2019))'],
      expectLog: ['api GET claim?kind=movie&name=Show (2020) {tmdb-1}', 'api PUT requests/<id>/posters/Show (2020) {tmdb-1}.jpg', 'api POST requests/<id>/posters', 'api POST requests/<id>/complete'] },
    { caseName: 'MOVIE finish while Posterflow also adds to IDarr: the note says so', chip: 'MOVIE', claim: CLAIM, idarr: true, steps: both('movie'),
      expect: ['✓ upload (posted 1 poster to The Spy (2019) and added it to IDarr (Posters))', '✓ complete'] },
    { caseName: 'SHOW orange with both on sends all three JPGs', chip: 'SHOW', claim: CLAIM, steps: both('show'),
      expect: ['SHOW · Show (2020): ✓ upload (posted 3 posters to The Spy (2019))', '✓ complete'],
      expectLog: ['api GET claim?kind=show&name=Show (2020) {tmdb-1}', 'api PUT requests/<id>/posters/Show (2020) {tmdb-1}.jpg', 'api PUT requests/<id>/posters/Show (2020) {tmdb-1} - Season 1.jpg', 'api PUT requests/<id>/posters/Show (2020) {tmdb-1} - Season 2.jpg', 'api POST requests/<id>/posters'] },
    { caseName: 'no claimed request: both steps skip in grey', chip: 'MOVIE', claim: { status: 'none' }, steps: both('movie'),
      expect: ['MOVIE · Show (2020): – upload skipped (none of your claimed requests matches "Show (2020)")', '– complete skipped'], forbidLog: /^api (PUT|POST)/ },
    { caseName: 'several claimed requests: red, nothing uploaded or completed', chip: 'MOVIE', claim: { status: 'several', type: 'request', labels: ['Dark (2017) S2', 'Dark (2017) S3'] }, steps: both('movie'),
      expect: ['✗ upload: 2 claimed requests match (Dark (2017) S2, Dark (2017) S3). Finish this one in Posterflow', 'complete skipped'], red: true, forbidLog: /^api (PUT|POST)/ },
    { caseName: 'a failed upload is red and the request is not completed', chip: 'MOVIE', claim: CLAIM, postFails: 'Discord post failed at message 1 of 1: boom', steps: both('movie'),
      expect: ['✗ upload: Discord post failed at message 1 of 1: boom', 'complete skipped'], red: true, forbidLog: /complete$/ },
    { caseName: 'a claimed list item with nothing to upload to is completed without an upload', chip: 'MOVIE', claim: ITEM, steps: both('movie'),
      expect: ['– upload skipped (The Spy · list is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow)', '✓ complete (The Spy · list)'], expectLog: ['api POST list-items/<id>/complete'], forbidLog: /^api PUT/ },
    { caseName: 'a claimed list item goes to IDarr when Posterflow adds to IDarr', chip: 'MOVIE', claim: Object.assign({}, ITEM, { upload: TO_IDARR }), idarr: true, steps: both('movie'),
      expect: ['✓ upload (', '✓ complete (The Spy · list)'], expectLog: ['api PUT list-items/<id>/posters/Show (2020) {tmdb-1}.jpg', 'api POST list-items/<id>/posters', 'api POST list-items/<id>/complete'] },
    { caseName: 'a request with nothing to upload to: nothing sent, not completed', chip: 'MOVIE', claim: Object.assign({}, CLAIM, { has_thread: false, upload: { will: '', aside: '', problem: 'The Spy (2019) has no Discord thread' } }), steps: both('movie'),
      expect: ['– upload skipped (The Spy (2019) has no Discord thread)', '– complete skipped (nothing was uploaded)'], forbidLog: /^api (PUT|POST)/ },
    // the finish uploads only what the request asks for
    { caseName: 'SHOW finish, request for one season: the rest is reported as not requested', chip: 'SHOW', claim: CLAIM, unwanted: /\{tmdb-1\}\.jpg$| - Season 1\.jpg$/, steps: both('show'),
      expect: ['✓ upload (posted 1 poster to The Spy (2019); 2 not requested)', '✓ complete'] },
    { caseName: 'SHOW finish, nothing it exported is requested: no upload, not completed', chip: 'SHOW', claim: CLAIM, unwanted: /./, steps: both('show'),
      expect: ['– upload skipped (The Spy (2019) asks for none of the 3 JPGs this finish exported)', '– complete skipped (nothing was uploaded)'], forbidLog: /complete$/ },
    // the gear's backdrop step: complete waits until the title's background is saved
    { caseName: 'backdrop step, background already saved: completed as usual', chip: 'MOVIE', claim: CLAIM, background: true, steps: both('movie', { backdrop: true }),
      expect: ['✓ upload (posted 1 poster to The Spy (2019))', '✓ backdrop (already saved)', '✓ complete (The Spy (2019))'], waiting: '',
      expectLog: ['api GET background Show (2020) {tmdb-1} - background.jpg', 'api POST requests/<id>/complete'] },
    { caseName: 'backdrop step, not saved yet: the upload goes out and the complete waits', chip: 'MOVIE', claim: CLAIM, steps: both('movie', { backdrop: true }),
      expect: ['✓ upload (', '✓ backdrop (not saved yet)', 'complete waits for the backdrop'], waiting: 'Waiting for backdrop: The Spy (2019)', forbidLog: /complete$/, noRed: true },
    { caseName: 'backdrop wait: the request is completed once the background is saved', chip: 'MOVIE', claim: CLAIM, background: 3, steps: both('movie', { backdrop: true }),
      expect: ['Completed The Spy (2019) (backdrop saved).'], waiting: '', expectLog: ['api POST requests/<id>/complete'], noRed: true },
    { caseName: 'backdrop wait: Complete now completes without a backdrop', chip: 'MOVIE', claim: CLAIM, clickWait: 'now', steps: both('movie', { backdrop: true }),
      expect: ['Completed The Spy (2019) (completed without a backdrop).'], waiting: '', expectLog: ['api POST requests/<id>/complete'] },
    { caseName: 'backdrop wait: X stops waiting and completes nothing', chip: 'MOVIE', claim: CLAIM, clickWait: 'stop', steps: both('movie', { backdrop: true }),
      expect: ['Stopped waiting for the backdrop: The Spy (2019). The request is still open.'], waiting: '', forbidLog: /complete$/ },
    { caseName: 'backdrop step with no request: a reminder that clears when the background is saved', chip: 'MOVIE', claim: { status: 'none' }, background: 3, steps: both('movie', { backdrop: true }),
      expect: ['Backdrop saved: Show (2020).'], waiting: '', forbidLog: /^api POST/ },
    { caseName: 'backdrop step with no request: the reminder stays while nothing is saved', chip: 'MOVIE', claim: { status: 'none' }, steps: both('movie', { backdrop: true }),
      expect: ['backdrop (not saved yet, reminder added)'], waiting: 'Backdrop not saved yet: Show (2020)', forbidLog: /^api POST/ },
    { caseName: 'backdrop step after a failed upload is skipped with complete', chip: 'MOVIE', claim: CLAIM, postFails: 'Discord post failed at message 1 of 1: boom', steps: both('movie', { backdrop: true }),
      expect: ['✗ upload: Discord post failed', 'backdrop and complete skipped'], red: true, waiting: '', forbidLog: /background|complete$/ },
    { caseName: 'backdrop step when Posterflow has no background folder: red, nothing completed', chip: 'MOVIE', claim: CLAIM, backgroundFails: 'No background export folder configured.', steps: both('movie', { backdrop: true }),
      expect: ['✗ backdrop: No background export folder configured.', 'complete skipped'], red: true, waiting: '', forbidLog: /complete$/ },
    { caseName: 'a wait kept from an earlier session is checked again after a reload', press: [], claim: CLAIM, background: true,
      seedWaits: [{ id: CLAIM.id, at: Date.now() - 3600000, where: 'requests', kind: 'movie', name: 'Show (2020) {tmdb-1}', label: 'The Spy (2019)', file: 'Show (2020) {tmdb-1} - background.jpg' }],
      expect: ['Completed The Spy (2019) (backdrop saved).'], waiting: '', expectLog: ['api GET claim?kind=movie&name=Show (2020) {tmdb-1}', 'api POST requests/<id>/complete'] },
    { caseName: 'no wait sits forever: after 3 days it is dropped in red and the request is left open', press: [], claim: CLAIM,
      seedWaits: [{ id: CLAIM.id, at: Date.now() - 3 * 86400000 - 60000, where: 'requests', kind: 'movie', name: 'Show (2020) {tmdb-1}', label: 'The Spy (2019)', file: 'Show (2020) {tmdb-1} - background.jpg' }],
      expect: ['Stopped waiting for the backdrop after 3 days: The Spy (2019). The request is still open.'], red: true, waiting: '', forbidLog: /^api /, },
    { caseName: 'a wait for a request completed elsewhere just clears', press: [], claim: { status: 'done', label: 'The Spy (2019)' }, background: true,
      seedWaits: [{ id: CLAIM.id, at: Date.now(), where: 'requests', kind: 'movie', name: 'Show (2020) {tmdb-1}', label: 'The Spy (2019)', file: 'Show (2020) {tmdb-1} - background.jpg' }],
      expect: ['Backdrop wait cleared: The Spy (2019) is no longer open.'], waiting: '', forbidLog: /^api POST/, noRed: true },
    // every open Photopea tab has this panel, and they share the one saved list of waits
    { caseName: 'two Photopea tabs: a wait added in another tab shows here too', press: [], otherTab: { after: 400, waits: [OTHER_WAIT] },
      expect: [], waiting: 'Waiting for backdrop: Other (2021)', forbidLog: /^api POST/ },
    { caseName: 'two Photopea tabs: this panel adds its wait to the other tab\'s, it does not save over it', chip: 'MOVIE', claim: CLAIM, steps: both('movie', { backdrop: true }),
      otherTab: { after: 50, waits: [OTHER_WAIT], quiet: true }, expect: ['complete waits for the backdrop'],
      waiting: 'Waiting for backdrop: Other (2021) | Waiting for backdrop: The Spy (2019)', stored: OTHER_WAIT.id + ' | ' + CLAIM.id },
    { caseName: 'two Photopea tabs: a wait another tab is completing is left to it', press: [], claim: CLAIM, background: true,
      seedWaits: () => [Object.assign({}, SPY_WAIT, { by: 'another-tab', byAt: Date.now() })],
      expect: [], waiting: 'Waiting for backdrop: The Spy (2019)', status: 'another Photopea tab is completing it', forbidLog: /^api (POST|GET claim)/ },
    { caseName: 'two Photopea tabs: a wait left by a tab that closed is taken over after a minute', press: [], claim: CLAIM, background: true,
      seedWaits: () => [Object.assign({}, SPY_WAIT, { by: 'closed-tab', byAt: Date.now() - 61000 })],
      expect: ['Completed The Spy (2019) (backdrop saved).'], waiting: '', expectLog: ['api POST requests/<id>/complete'] },
    { caseName: 'two Photopea tabs: a wait another tab finished goes from here too', press: [], claim: CLAIM, seedWaits: [SPY_WAIT],
      otherTab: { after: 400, waits: [] }, expect: [], waiting: '', stored: '', forbidLog: /^api POST/ },
    // the Upload and Complete buttons: the first press asks and shows, the second does it
    { caseName: 'Upload button, first press: shows the pick, uploads nothing', press: ['upload'], claim: CLAIM, files: FILES,
      expect: ['Upload: 2 posters for The Spy (2019): main (2m ago), Season 2 (3d ago). Press Upload again to post to Discord.'], asks: 1, noRed: true,
      expectLog: ['api GET upload?name=Show (2020) {tmdb-1}'], forbidLog: /^api (PUT|POST)/ },
    { caseName: 'Upload button, second press: uploads the pick', press: ['upload', 'upload'], claim: CLAIM, files: FILES,
      expect: ['Upload: posted 2 posters to The Spy (2019).'], asks: 0, noRed: true, expectLog: ['api POST requests/<id>/upload'], forbidLog: /^api PUT|complete$/ },
    { caseName: 'Upload button says what Posterflow is set to do, and what cannot run', press: ['upload'], idarr: true, files: FILES,
      claim: Object.assign({}, CLAIM, { has_thread: false, upload: { will: 'add to IDarr (Posters)', aside: 'No Discord thread, so nothing goes to Discord', problem: '' } }),
      expect: ['Press Upload again to add to IDarr (Posters). No Discord thread, so nothing goes to Discord.'], asks: 1 },
    { caseName: 'Upload button reports the upload in Posterflow\'s words', press: ['upload', 'upload'], idarr: true, claim: Object.assign({}, CLAIM, { upload: { will: 'post to Discord and add to IDarr (Posters)', aside: '', problem: '' } }), files: FILES,
      expect: ['Upload: posted 2 posters to The Spy (2019) and added them to IDarr (Posters).'], asks: 0, noRed: true },
    { caseName: 'Upload button, a list item is uploaded as one', press: ['upload', 'upload'], idarr: true, claim: Object.assign({}, ITEM, { upload: TO_IDARR }), files: FILES,
      expect: ['Upload: '], expectLog: ['api POST list-items/<id>/upload'] },
    { caseName: 'Upload button, no export in the folders', press: ['upload', 'upload'], claim: CLAIM, files: [],
      expect: ['Upload: no export for The Spy (2019) in the Posterflow export folders.'], asks: 0, noRed: true, forbidLog: /^api (PUT|POST)/ },
    { caseName: 'Upload button, no export folder set', press: ['upload'], claim: CLAIM, files: FILES, folders: 0,
      expect: ['Upload: no export folder is set in Posterflow.'], asks: 0, forbidLog: /^api (PUT|POST)/ },
    { caseName: 'Upload button, no claimed request', press: ['upload'], claim: { status: 'none' },
      expect: ['Upload: none of your claimed requests matches "Show (2020)".'], asks: 0, noRed: true, forbidLog: /^api (PUT|POST)/ },
    { caseName: 'Upload button, nothing the settings allow', press: ['upload'], claim: ITEM, files: FILES,
      expect: ['Upload: The Spy · list is a list item. It has no Discord thread, and Adds to IDarr is off in Posterflow.'], asks: 0, forbidLog: /^api (PUT|POST)/ },
    { caseName: 'Upload button, several claimed requests: red', press: ['upload'], claim: { status: 'several', type: 'request', labels: ['Dark (2017) S2', 'Dark (2017) S3'] },
      expect: ['Upload: 2 claimed requests match (Dark (2017) S2, Dark (2017) S3). Finish this one in Posterflow.'], red: true, forbidLog: /^api (PUT|POST)/ },
    { caseName: 'Upload button, a failed upload is red', press: ['upload', 'upload'], claim: CLAIM, files: FILES, postFails: 'Discord post failed at message 1 of 1: boom',
      expect: ['Upload: Discord post failed at message 1 of 1: boom.'], red: true, asks: 0 },
    { caseName: 'Complete button, first press: names the request, completes nothing', press: ['complete'], claim: CLAIM,
      expect: ['Complete: The Spy (2019). Press Complete again to mark it complete.'], asks: 1, noRed: true, expectLog: ['api GET claim?name=Show (2020) {tmdb-1}'], forbidLog: /^api POST/ },
    { caseName: 'Complete button, second press: completes the request', press: ['complete', 'complete'], claim: CLAIM,
      expect: ['Complete: The Spy (2019) is marked complete.'], asks: 0, noRed: true, expectLog: ['api POST requests/<id>/complete'] },
    { caseName: 'Complete button, a list item is completed as one', press: ['complete', 'complete'], claim: ITEM,
      expect: ['Complete: The Spy · list is marked complete.'], expectLog: ['api POST list-items/<id>/complete'] },
    { caseName: 'Complete button, a refusal is red', press: ['complete', 'complete'], claim: CLAIM, completeFails: 'Only in-progress requests can be marked as complete',
      expect: ['Complete: Only in-progress requests can be marked as complete.'], red: true },
    { caseName: 'one press of each button only asks', press: ['upload', 'complete'], claim: CLAIM, files: FILES,
      expect: ['Complete: The Spy (2019). Press Complete again'], asks: 2, forbidLog: /^api (PUT|POST)/ },
  );
  let fails = 0;
  const only = process.env.CASE;   // CASE="Upload button" runs just the cases whose name contains it
  for (const c of cases.filter((k) => !only || k.caseName.includes(only))) {
    const r = await run(c.caseName, c);
    console.log((r.ok ? 'PASS ' : 'FAIL ') + r.caseName);
    if (!r.ok) { fails++; console.log('   notes:', JSON.stringify(r.notes, null, 1)); console.log('   log:', r.log, r.why || ''); }
    else if (r.unhandled.length) console.log('   (unhandled scripts:', r.unhandled.length + ')');
  }
  process.exit(fails ? 1 : 0);
})();
