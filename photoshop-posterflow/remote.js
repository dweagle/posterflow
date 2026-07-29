// Remote Posterflow link: the panel polls the server's photoshop-queue for PSDs exported with the
// web UI's "PS" target, downloads them to the plugin temp folder, and opens them. Save/JPG/Logo for
// those documents PUT back to the server's existing endpoints (Bearer = the app password — the
// server verifies the raw password against its stored hash). Config lives in localStorage.
// Platform note: plain http:// URLs work on WINDOWS ONLY — macOS (ATS) requires https.
'use strict';

const { app, core } = require('photoshop');
const uxpfs = require('uxp').storage.localFileSystem;
const formats = require('uxp').storage.formats;

const CFG_KEY = 'posterflow.remote';   // { url, password, enabled }

function getConfig() {
  try { return JSON.parse(localStorage.getItem(CFG_KEY) || '{}') || {}; } catch (_) { return {}; }
}
function setConfig(cfg) {
  try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); } catch (_) {}
}
const baseUrl = (cfg) => String(cfg.url || '').trim().replace(/\/+$/, '');
const isConfigured = (cfg) => !!(cfg.enabled && baseUrl(cfg));
const authHeaders = (cfg) => (cfg.password ? { Authorization: 'Bearer ' + cfg.password } : {});

let clientId = '';
const getClientId = () => {
  if (!clientId) clientId = 'ps-' + Math.random().toString(36).slice(2, 8);
  return clientId;
};

// One poll: returns (and thereby CLAIMS) the pending open requests. Throws on network/auth errors.
async function pollQueue() {
  const cfg = getConfig();
  if (!isConfigured(cfg)) return [];
  const r = await fetch(baseUrl(cfg) + '/api/maker-tools/photoshop-queue?client=' + encodeURIComponent(getClientId()), {
    headers: authHeaders(cfg),
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const j = await r.json();
  return j.items || [];
}

// Download a queued PSD (the item carries a ready-signed psd_url) into the plugin temp folder and
// open it. Returns { doc, entry } — the caller registers doc.id → context for save-back routing.
async function openQueued(item) {
  const cfg = getConfig();
  const r = await fetch(baseUrl(cfg) + item.psd_url, { headers: authHeaders(cfg) });
  if (!r.ok) throw new Error('download failed (HTTP ' + r.status + ')');
  const buf = await r.arrayBuffer();
  const tmp = await uxpfs.getTemporaryFolder();
  const entry = await tmp.createFile(item.filename, { overwrite: true });
  await entry.write(buf, { format: formats.binary });
  let doc = null;
  await core.executeAsModal(async () => { doc = await app.open(entry); }, { commandName: 'Open Posterflow PSD' });
  if (!doc) throw new Error('Photoshop did not open the document');
  return { doc, entry };
}

// PUT raw bytes to a server path ('/api/maker-tools/…'). Throws on non-2xx.
async function putBytes(path, bytes) {
  const cfg = getConfig();
  const r = await fetch(baseUrl(cfg) + path, {
    method: 'PUT',
    headers: Object.assign({ 'Content-Type': 'application/octet-stream' }, authHeaders(cfg)),
    body: bytes,
  });
  if (!r.ok) throw new Error('upload failed (HTTP ' + r.status + ')');
}

// Fresh temp file for a derived export (JPG/PNG) of a remote doc.
async function tempFile(name) {
  const tmp = await uxpfs.getTemporaryFolder();
  return tmp.createFile(name, { overwrite: true });
}

const tempFolder = () => uxpfs.getTemporaryFolder();
const readBytes = (entry) => entry.read({ format: formats.binary });

module.exports = { getConfig, setConfig, isConfigured, baseUrl, pollQueue, openQueued, putBytes, tempFile, tempFolder, readBytes };
