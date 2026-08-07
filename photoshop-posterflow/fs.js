// Per-style image-export folder, remembered across sessions via a UXP persistent token.
// CL2K and MM2K route to different folders (matching Posterflow's psd_image_export_folder[_mm2k]
// settings), so tokens are keyed by style. localStorage holds the token string; if it's missing or
// stale (folder moved/deleted/perm-changed → getEntryForPersistentToken throws), we re-prompt with
// getFolder(). PSD save never comes here — doc.save() overwrites the opened file directly.
'use strict';

const fs = require('uxp').storage.localFileSystem;
const formats = require('uxp').storage.formats;

const tokenKey = (style) => 'posterflow.imageFolder.' + (style || 'default');

// Returns a Folder entry, or null if the user cancelled the picker. Prompts only when there's no
// valid remembered folder (or forcePick to deliberately re-choose).
async function getImageFolder(style, { forcePick = false } = {}) {
  const lsKey = tokenKey(style);
  if (!forcePick) {
    let tok = null;
    try { tok = localStorage.getItem(lsKey); } catch (_) {}
    if (tok) {
      try { return await fs.getEntryForPersistentToken(tok); }
      catch (_) { try { localStorage.removeItem(lsKey); } catch (_e) {} }   // stale → fall through to re-pick
    }
  }
  const folder = await fs.getFolder();
  if (!folder) return null;
  try { localStorage.setItem(lsKey, await fs.createPersistentToken(folder)); } catch (_) {}
  return folder;
}

function clearImageFolder(style) { try { localStorage.removeItem(tokenKey(style)); } catch (_) {} }

// Shared ask-once-remember logic for the single-folder (not per-style) export buttons below —
// Logo, Textless, Square Art — each keyed by its own localStorage entry.
async function _getOrPickFolder(lsKey, forcePick) {
  if (!forcePick) {
    let tok = null;
    try { tok = localStorage.getItem(lsKey); } catch (_) {}
    if (tok) {
      try { return await fs.getEntryForPersistentToken(tok); }
      catch (_) { try { localStorage.removeItem(lsKey); } catch (_e) {} }
    }
  }
  const folder = await fs.getFolder();
  if (!folder) return null;
  try { localStorage.setItem(lsKey, await fs.createPersistentToken(folder)); } catch (_) {}
  return folder;
}

// Logo export folder — one folder, not per style (logos only exist on CL2K templates).
const LOGO_KEY = 'posterflow.logoFolder';
function getLogoFolder({ forcePick = false } = {}) { return _getOrPickFolder(LOGO_KEY, forcePick); }

// Textless export folder — one folder, applies to both CL2K and MM2K (every template has a POSTER group).
const TEXTLESS_KEY = 'posterflow.textlessFolder';
function getTextlessFolder({ forcePick = false } = {}) { return _getOrPickFolder(TEXTLESS_KEY, forcePick); }

// Square Art export folder — one folder, applies to both styles.
const SQUAREART_KEY = 'posterflow.squareartFolder';
function getSquareartFolder({ forcePick = false } = {}) { return _getOrPickFolder(SQUAREART_KEY, forcePick); }

// Forget every remembered folder (image styles + logo + textless + square art) — the next export
// of each prompts again.
function clearAllFolders() {
  ['CL2K', 'MM2K', 'default'].forEach((s) => clearImageFolder(s));
  [LOGO_KEY, TEXTLESS_KEY, SQUAREART_KEY].forEach((k) => { try { localStorage.removeItem(k); } catch (_) {} });
}

// Write raw bytes into a folder (used when a server upload falls back to a local save).
async function writeFileBytes(folder, name, bytes) {
  const f = await folder.createFile(name, { overwrite: true });
  await f.write(bytes, { format: formats.binary });
}

module.exports = {
  getImageFolder, clearImageFolder, getLogoFolder, getTextlessFolder, getSquareartFolder,
  clearAllFolders, writeFileBytes,
};
