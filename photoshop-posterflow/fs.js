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

// Logo export folder — one folder, not per style (logos only exist on CL2K templates).
const LOGO_KEY = 'posterflow.logoFolder';
async function getLogoFolder({ forcePick = false } = {}) {
  if (!forcePick) {
    let tok = null;
    try { tok = localStorage.getItem(LOGO_KEY); } catch (_) {}
    if (tok) {
      try { return await fs.getEntryForPersistentToken(tok); }
      catch (_) { try { localStorage.removeItem(LOGO_KEY); } catch (_e) {} }
    }
  }
  const folder = await fs.getFolder();
  if (!folder) return null;
  try { localStorage.setItem(LOGO_KEY, await fs.createPersistentToken(folder)); } catch (_) {}
  return folder;
}

// Square Art export folder — one folder, not per style.
const SQUAREART_KEY = 'posterflow.squareartFolder';
async function getSquareartFolder({ forcePick = false } = {}) {
  if (!forcePick) {
    let tok = null;
    try { tok = localStorage.getItem(SQUAREART_KEY); } catch (_) {}
    if (tok) {
      try { return await fs.getEntryForPersistentToken(tok); }
      catch (_) { try { localStorage.removeItem(SQUAREART_KEY); } catch (_e) {} }
    }
  }
  const folder = await fs.getFolder();
  if (!folder) return null;
  try { localStorage.setItem(SQUAREART_KEY, await fs.createPersistentToken(folder)); } catch (_) {}
  return folder;
}

// Poster export folder — one folder, not per style.
const POSTER_KEY = 'posterflow.posterFolder';
async function getPosterFolder({ forcePick = false } = {}) {
  if (!forcePick) {
    let tok = null;
    try { tok = localStorage.getItem(POSTER_KEY); } catch (_) {}
    if (tok) {
      try { return await fs.getEntryForPersistentToken(tok); }
      catch (_) { try { localStorage.removeItem(POSTER_KEY); } catch (_e) {} }
    }
  }
  const folder = await fs.getFolder();
  if (!folder) return null;
  try { localStorage.setItem(POSTER_KEY, await fs.createPersistentToken(folder)); } catch (_) {}
  return folder;
}

// Forget every remembered folder (image styles + logo + square art + poster) — the next export
// prompts again.
function clearAllFolders() {
  ['CL2K', 'MM2K', 'default'].forEach((s) => clearImageFolder(s));
  try { localStorage.removeItem(LOGO_KEY); } catch (_) {}
  try { localStorage.removeItem(SQUAREART_KEY); } catch (_) {}
  try { localStorage.removeItem(POSTER_KEY); } catch (_) {}
}

// Write raw bytes into a folder (used when a server upload falls back to a local save).
async function writeFileBytes(folder, name, bytes) {
  const f = await folder.createFile(name, { overwrite: true });
  await f.write(bytes, { format: formats.binary });
}

module.exports = { getImageFolder, clearImageFolder, getLogoFolder, getSquareartFolder, getPosterFolder, clearAllFolders, writeFileBytes };
