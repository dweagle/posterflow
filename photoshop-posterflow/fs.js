// Export folders, remembered across sessions via UXP persistent tokens (the token strings live in
// localStorage). A remembered folder that can't be reached opens the picker, and stays remembered
// until another one is picked. The panel names the folder before any picker opens (setAsker): the
// picker itself cannot say what it is for. PSD save never comes here — doc.save() overwrites the opened file directly.
'use strict';

const fs = require('uxp').storage.localFileSystem;
const formats = require('uxp').storage.formats;

// The panel hears which remembered folder can't be reached, just before the picker opens.
let onMissing = null;
function setOnMissing(fn) { onMissing = fn; }
// The panel asks for a folder by name before its picker opens: asker({ name, hint }) resolves true to go
// on, false to cancel.
let asker = null;
function setAsker(fn) { asker = fn; }

// A remembered folder, only if its path still exists. Checked at most once a minute per folder.
const CHECK_MS = 60000;
const checked = {};   // token → { folder, at }
async function liveFolder(tok) {
  const c = checked[tok];
  if (c && Date.now() - c.at < CHECK_MS) return c.folder;
  const folder = await fs.getEntryForPersistentToken(tok);
  await folder.getMetadata();   // throws when the path is gone
  checked[tok] = { folder, at: Date.now() };
  return folder;
}

// The folder remembered under `key`, else the picker (forcePick goes straight to it). `what` names
// the folder: { name, hint }. Returns a Folder entry, or null if the ask or the picker was cancelled.
async function remembered(key, what, forcePick) {
  if (!forcePick) {
    let tok = null;
    try { tok = localStorage.getItem(key); } catch (_) {}
    if (tok) {
      try { return await liveFolder(tok); }
      catch (_) { if (onMissing) onMissing(what.name); }
    }
  }
  if (asker && !(await asker(what))) return null;
  const folder = await fs.getFolder();
  if (!folder) return null;
  try { localStorage.setItem(key, await fs.createPersistentToken(folder)); } catch (_) {}
  return folder;
}

// Image folder, one per style: CL2K and MM2K export to different folders (matching Posterflow's
// psd_image_export_folder[_mm2k] settings).
const tokenKey = (style) => 'posterflow.imageFolder.' + (style || 'default');
const getImageFolder = (style, { forcePick = false } = {}) =>
  remembered(tokenKey(style), { name: (style ? style + ' ' : '') + 'image folder', hint: 'where exported poster JPGs go' }, forcePick);
function clearImageFolder(style) { try { localStorage.removeItem(tokenKey(style)); } catch (_) {} }

// Logo, Square Art, Poster and backgrounds folders: one each, not per style.
const LOGO_KEY = 'posterflow.logoFolder';
const SQUAREART_KEY = 'posterflow.squareartFolder';
const POSTER_KEY = 'posterflow.posterFolder';
const BACKGROUND_KEY = 'posterflow.backgroundFolder';
const getLogoFolder = ({ forcePick = false } = {}) => remembered(LOGO_KEY, { name: 'logo folder', hint: 'where logo PNGs go' }, forcePick);
const getSquareartFolder = ({ forcePick = false } = {}) => remembered(SQUAREART_KEY, { name: 'Square Art folder', hint: 'where Square Art crops go' }, forcePick);
const getPosterFolder = ({ forcePick = false } = {}) => remembered(POSTER_KEY, { name: 'poster folder', hint: 'where the Poster button\'s JPGs go' }, forcePick);
const getBackgroundFolder = ({ forcePick = false } = {}) => remembered(BACKGROUND_KEY, { name: 'backgrounds folder', hint: 'where Save BG puts backgrounds' }, forcePick);

// Forget every remembered folder (image styles + logo + square art + poster) — the next export
// prompts again.
function clearAllFolders() {
  ['CL2K', 'MM2K', 'default'].forEach((s) => clearImageFolder(s));
  try { localStorage.removeItem(LOGO_KEY); } catch (_) {}
  try { localStorage.removeItem(SQUAREART_KEY); } catch (_) {}
  try { localStorage.removeItem(POSTER_KEY); } catch (_) {}
  try { localStorage.removeItem(BACKGROUND_KEY); } catch (_) {}
}

// Write raw bytes into a folder; returns the written file.
async function writeFileBytes(folder, name, bytes) {
  try {
    const f = await folder.createFile(name, { overwrite: true });
    await f.write(bytes, { format: formats.binary });
    return f;
  } catch (e) {
    const raw = e && e.message ? e.message : (e ? String(e) : '');
    throw new Error('Export failed. The "' + folder.name + '" folder may be unreachable. Reload the plugin or wait '
      + CHECK_MS / 1000 + ' seconds, then try again.' + (raw ? ' Photoshop reported: ' + raw : ''));
  }
}

module.exports = { getImageFolder, clearImageFolder, getLogoFolder, getSquareartFolder, getPosterFolder, getBackgroundFolder, clearAllFolders, setOnMissing, setAsker, writeFileBytes };
