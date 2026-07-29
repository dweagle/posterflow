// PSD save + JPG export. Both document writes run inside core.executeAsModal (required for anything
// that edits the document / writes a file through the DOM). PSD save overwrites the file the doc was
// opened from (doc.save() = Ctrl+S). JPG is written as a COPY (asCopy=true) so the open doc's
// dirty/saved state and format are untouched, into the per-style image folder from fs.js.
'use strict';

const { core } = require('photoshop');
const { getImageFolder } = require('./fs');

// Photoshop's JPEG quality is 0–12 (12 = max). 10 ≈ the Photopea panel's jpg:0.90 — high, small-ish.
const JPG_QUALITY = 10;

async function savePsd(doc) {
  await core.executeAsModal(async () => { await doc.save(); }, { commandName: 'Save poster PSD' });
}

// baseName already has its extension stripped; suffix is e.g. ' - Season 3' (from activeSuffix).
// forcePick re-prompts for the folder even if one is remembered (Alt-click JPG). Returns
// { ok, filename?, folderName?, reason? } — reason 'cancelled' when the folder picker was dismissed.
async function exportJpg(doc, style, baseName, suffix, { forcePick = false } = {}) {
  const folder = await getImageFolder(style, { forcePick });
  if (!folder) return { ok: false, reason: 'cancelled' };
  const filename = baseName + suffix + '.jpg';
  const file = await folder.createFile(filename, { overwrite: true });
  await core.executeAsModal(async () => {
    await doc.saveAs.jpg(file, { quality: JPG_QUALITY }, true);
  }, { commandName: 'Export poster JPG' });
  return { ok: true, filename, folderName: folder.name };
}

// ---- Remote (Posterflow-queued) documents ----
// These docs came from the server via remote.js: 💾 saves the temp file then PUTs it back over the
// server's PSD; JPG renders to a temp file and PUTs it to the server's image-exports folder.
const R = require('./remote');

async function savePsdRemote(doc, ctx) {
  await core.executeAsModal(async () => { await doc.save(); }, { commandName: 'Save poster PSD' });   // writes ctx.entry (the temp file it was opened from)
  const bytes = await R.readBytes(ctx.entry);
  await R.putBytes('/api/maker-tools/psd-exports/' + encodeURIComponent(ctx.filename) + '?style=' + encodeURIComponent(ctx.style || ''), bytes);
}

async function exportJpgRemote(doc, ctx, baseName, suffix) {
  const filename = baseName + suffix + '.jpg';
  const file = await R.tempFile(filename);
  await core.executeAsModal(async () => {
    await doc.saveAs.jpg(file, { quality: JPG_QUALITY }, true);
  }, { commandName: 'Export poster JPG' });
  const bytes = await R.readBytes(file);
  await R.putBytes('/api/maker-tools/image-exports/' + encodeURIComponent(filename) + '?style=' + encodeURIComponent(ctx.style || ''), bytes);
  return { ok: true, filename, folderName: 'Posterflow' };
}

module.exports = { savePsd, exportJpg, savePsdRemote, exportJpgRemote, JPG_QUALITY };
