// jsdom check of the Square Art edge keeper in BOTH Photopea panels: arm the crop tab by faking the SQTEMP echo, feed
// selection readings, and check that a repeated off-edge reading triggers exactly one re-select while hand-drawn shapes
// are left alone. Run from the repo root:
//   NODE_PATH=frontend/node_modules node photopea-posterflow/test/squareart-watch-harness.js frontend/public/photopea-plugin.html PFL
//   NODE_PATH=frontend/node_modules node photopea-posterflow/test/squareart-watch-harness.js photopea-posterflow/photopea-posterflow.html SZ
const { JSDOM } = require('jsdom'); const path = require('path');
const [,, file, P] = process.argv;
(async () => {
  const sent = [];
  let dom;
  const post = (msg) => setTimeout(() => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data: msg })), 0);
  dom = await JSDOM.fromFile(path.resolve(file), { url: 'http://localhost/x.html', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) { window.postMessage = (m) => { sent.push(m); if (m.includes("'" + P + "SQSNAP:")) { const mm = m.match(/SQSNAP:(\d+),(\w+)/); post(P + 'SQSNAP:' + mm[1] + ',' + mm[2]); } }; } });
  const w = dom.window;
  await new Promise((r) => setTimeout(r, 120));
  post(P + 'SQTEMP:Square Art Crop');          // arms: watcher starts, default preset placed
  await new Promise((r) => setTimeout(r, 120));
  post(P + 'SQSET:950');                        // the preset's own echo: the panel remembers 950
  const ticks = () => sent.filter((m) => m.includes("'" + P + "SQW:'")).length;
  await new Promise((r) => setTimeout(r, 1400));
  const t1 = ticks();
  // a square dragged past the left edge, reported twice
  post(P + 'SQW:-60,189,890,1139,950,1425'); await new Promise((r) => setTimeout(r, 50));
  const before = sent.filter((m) => m.includes('SQSNAP:')).length;
  post(P + 'SQW:-60,189,890,1139,950,1425'); await new Promise((r) => setTimeout(r, 80));
  const after = sent.filter((m) => m.includes('SQSNAP:')).length;
  const fix = sent.filter((m) => m.includes('SQSNAP:')).pop() || '';
  // a hand-drawn shape in the middle: never touched
  post(P + 'SQW:100,100,700,400,950,1425'); post(P + 'SQW:100,100,700,400,950,1425'); await new Promise((r) => setTimeout(r, 80));
  const shapeFixes = sent.filter((m) => m.includes('SQSNAP:')).length - after;
  // clipped at the right, twice
  post(P + 'SQW:400,100,950,800,950,1425'); await new Promise((r) => setTimeout(r, 50)); post(P + 'SQW:400,100,950,800,950,1425'); await new Promise((r) => setTimeout(r, 80));
  const clip = sent.filter((m) => m.includes('SQSNAP:')).pop() || '';
  const notes = Array.from(w.document.querySelectorAll('.notes .msg')).map((n) => n.textContent);
  const ok = t1 >= 2 && before === 0 && after === 1 && /select\(\[\[0,189\]/.test(fix) && shapeFixes === 0 && /select\(\[\[0,100\].*SQSNAP:950,clipped/.test(clip);
  console.log((ok ? 'PASS ' : 'FAIL ') + P + ': ticks while armed=' + t1 + ' first-sighting fixes=' + before + ' second-sighting fixes=' + after + ' shape fixes=' + shapeFixes);
  if (!ok) { console.log(' fix:', fix.slice(0, 160)); console.log(' clip:', clip.slice(0, 160)); console.log(' notes:', notes.slice(0, 4)); }
  process.exit(ok ? 0 : 1);
})();
