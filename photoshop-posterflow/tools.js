// Place Logo / Fit Poster / Trim — document-editing tools, each inside executeAsModal.
// Placement flow mirrors the Photopea panel: read the SELECTED layer's bounds + canvas, compute the
// target geometry, scale by percent (anchor MIDDLECENTER, so top-left moves), re-read the new bounds,
// then translate by (target − current). UXP bounds are in pixels, so there's no ruler-units dance and
// no UnitValue sign-flip trap — all arithmetic is plain JS numbers.
'use strict';

const { app, core, action } = require('photoshop');
const G = require('./geometry');
const { treeFromList } = require('./model');
const { JPG_QUALITY, writeViaTemp } = require('./save');

// Neutral density: the export's logo formula has a transparency term that needs the layer's pixels
// (fragile to read for one layer), so — like the Photopea panel — we skip it and use 0.30 (no sparse
// boost / dense shrink). Aspect ratio + pixel area (the dominant factors) are still exact.
const LOGO_DENSITY = 0.30;

// The doc's lowest horizontal guide (the template's bottom-fade line), or 0 when it has none.
function lowestGuideY(doc, constants) {
  let y = 0;
  try {
    const gs = doc.guides;
    for (let i = 0; i < gs.length; i++) {
      const g = gs[i];
      if (g.direction === constants.Direction.HORIZONTAL && g.coordinate > y) y = g.coordinate;
    }
  } catch (_) {}
  return y;
}
// Fit Poster's bottom line: that guide, or canvas − 25.
function bottomGuideY(doc, constants) {
  const y = lowestGuideY(doc, constants);
  return y > 0 ? y : doc.height - 25;
}

// ---- Coverage: blank canvas in the poster layers a finish is about to export ----
// See-through pixels of `layer` inside `box` (G.blankPixels, in document pixels), or null when
// Photoshop can't hand over the layer's pixels; the check then rests on the bounds alone.
async function blankInBox(doc, layer, box) {
  const imaging = require('photoshop').imaging;
  if (!imaging || !imaging.getPixels) return null;
  let img = null;
  try {
    const res = await imaging.getPixels({ documentID: doc.id, layerID: layer.id, sourceBounds: box, componentSize: 8, applyAlpha: false });
    img = res && res.imageData;
    const sb = res && res.sourceBounds;
    // The bounds already cover the box, so a trimmed or scaled-down answer isn't the layer's real pixels.
    if (!img || !sb || res.level || sb.left > box.left || sb.top > box.top || sb.right < box.right || sb.bottom < box.bottom) return null;
    if (!img.hasAlpha) return { count: 0 };
    const hit = G.blankPixels(await img.getData({ chunky: true }), img.width, img.height, img.components);
    return { count: hit.count, x0: hit.x0 + sb.left, x1: hit.x1 + sb.left, y0: hit.y0 + sb.top, y1: hit.y1 + sb.top };
  } catch (_) { return null; }
  finally { try { if (img) img.dispose(); } catch (_) {} }
}
// Each target ({ label, layer, why? }; layer null = missing, why says so) must cover the box Fit Poster
// fits into. Bounds first; the pixels are only read when the bounds pass. Returns { skipped } for a
// document with no horizontal guide, else { problems: [text], boundsOnly: [label] }.
async function checkCoverage(doc, constants, targets) {
  const guideY = lowestGuideY(doc, constants);
  if (!(guideY > 0)) return { skipped: 'no horizontal guide' };
  const box = G.coverageBox(doc.width, guideY);
  const problems = [], boundsOnly = [];
  await core.executeAsModal(async () => {
    for (const { label, layer, why } of targets) {
      if (!layer) { problems.push(label + ': ' + (why || 'layer not found')); continue; }
      let b = null;
      try { b = layer.boundsNoEffects || layer.bounds; } catch (_) {}
      if (!b || !(b.right - b.left > 0 && b.bottom - b.top > 0)) { problems.push(label + ': the layer is empty'); continue; }
      const gaps = G.coverageGaps(b, box);
      if (gaps.length) {
        problems.push(label + ': blank canvas (' + G.coverageGapText(gaps) + ')');
        continue;
      }
      const blank = await blankInBox(doc, layer, box);
      if (!blank) boundsOnly.push(label);
      else if (blank.count) problems.push(label + ': see-through area inside the poster (~' + blank.count + ' px, x ' + blank.x0 + ' to ' + blank.x1 + ', y ' + blank.y0 + ' to ' + blank.y1 + ')');
    }
  }, { commandName: 'Check poster coverage' });
  return { problems, boundsOnly };
}

// mode: 'logo' | 'fit'. Returns { ok } or { ok:false, reason:'no-layer' }.
async function placeSelected(doc, mode, constants) {
  const layer = doc.activeLayers && doc.activeLayers[0];
  if (!layer) return { ok: false, reason: 'no-layer' };
  const cw = doc.width, ch = doc.height;
  await core.executeAsModal(async () => {
    const b0 = layer.bounds;
    const lw = b0.right - b0.left, lh = b0.bottom - b0.top;
    if (!(lw > 0 && lh > 0)) throw new Error('selected layer has empty bounds');
    const res = mode === 'logo'
      ? G.computeLogoGeometry(lw, lh, cw, ch, LOGO_DENSITY)
      : G.computePosterFitGeometry(lw, lh, cw, bottomGuideY(doc, constants));
    const sc = (res.width / lw) * 100;
    await layer.scale(sc, sc, constants.AnchorPosition.MIDDLECENTER);
    const b1 = layer.bounds;                                    // re-measure after the resize
    await layer.translate(res.left - b1.left, res.top - b1.top); // deltas in px
  }, { commandName: mode === 'logo' ? 'Place logo' : 'Fit poster' });
  return { ok: true };
}

// Crop to the exact canvas → discards every layer's off-canvas pixels (shrinks the saved PSD). Canvas
// size + all layers preserved; no visible change. Same trick as the Photopea panel's d.crop([0,0,w,h]).
async function trim(doc) {
  const w = Math.round(doc.width), h = Math.round(doc.height);
  await core.executeAsModal(async () => {
    await doc.crop({ left: 0, top: 0, right: w, bottom: h });
  }, { commandName: 'Trim off-canvas pixels' });
}

// ---- Collection logo placement (ported from CL2K batch export.jsx) ----
// For the "c" batch export: a temporary COPY of the logo is placed with its bottom on the collection
// ruler and auto-shrunk (aspect kept, bottom anchored) if taller than the allowed height, then
// deleted after the export — the real logo layers are never altered. Rulers are proportions of
// canvas height (the .jsx used 181 / 400 px-from-bottom on a 1500-tall canvas).
//
// The copy is made with batchPlay (duplicate by layer ID → show → mergeLayersNew), NOT DOM
// layer.duplicate(): UXP's DOM duplication proved unreliable in every form (stalls, unremovable
// copies, child-visibility edits not sticking). Merging flattens the copy to ONE raster layer, so
// the placement uses only ops this plugin already relies on daily (scale/translate on an art layer,
// like Place Logo), and cleanup deletes that one layer by ID.
// Direct LOGO children named c/c1/c2… are a manual collection-only logo: when present, ONLY those
// layers are duplicated; otherwise the whole LOGO group is duplicated (its visible content merges).
const TMP_LOGO = '__PFLTMPLOGO__';   // marker for temp copies (+ orphans of old runs — cleanup sweeps)
const COLLECTION_BOTTOM = 181 / 1500;   // ruler the logo's bottom edge sits on
const LOGO_TOP_LIMIT = 400 / 1500;      // the logo may never reach above this line

// Path to the LOGO group as indexes from the top, or null. Top level first, then one whole-layer-list
// call; the layers are walked one by one only if that call fails or does not line up with the document.
const LOGO_RE = /^\s*logos?\s*$/i;
async function findLogoPath(doc, constants) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  const top = doc.layers;
  for (let i = 0; i < top.length; i++) if (isGroup(top[i]) && LOGO_RE.test(top[i].name)) return [i];
  const search = (layers, at) => {   // first match, a group before its children
    for (let i = 0; i < layers.length; i++) {
      if (!isGroup(layers[i])) continue;
      if (LOGO_RE.test(layers[i].name)) return at.concat(i);
      const f = search(layers[i].layers, at.concat(i));
      if (f) return f;
    }
    return null;
  };
  let tree = null;
  try { tree = await readLayerTree(doc, constants); } catch (_) {}
  if (!tree) return search(top, []);
  const p = search(tree, []);
  if (!p) return null;
  const listed = layerAtPath({ layers: tree }, p), live = layerAtPath(doc, p);
  return live && live.id === listed.id ? p : search(top, []);
}
function layerAtPath(doc, p) {
  let L = null, list = doc.layers;
  for (const i of p) { L = list && list[i]; if (!L) return null; list = L.layers; }
  return L;
}
async function findLogoGroup(doc, constants) {
  const p = await findLogoPath(doc, constants);
  return p ? layerAtPath(doc, p) : null;
}

// Returns { placed, copyId, warning, note } — warning set when the logo couldn't be placed (the
// export continues bare); note is informational (e.g. auto-shrink applied). Best-effort by design:
// ANY failure cleans up and returns a warning instead of throwing, so a placement problem can never
// wedge or abort the batch.
async function placeCollectionLogo(doc, constants) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  const out = { placed: false, copyId: null, warning: null, note: null };
  await core.executeAsModal(async () => {
    let lg = null;
    try {
      lg = await findLogoGroup(doc, constants);
      if (!lg) { out.warning = 'No LOGO group; the Collection poster was exported without a logo.'; return; }
      if (!lg.visible) { out.warning = 'The LOGO group is hidden; the Collection poster was exported without a logo.'; return; }
      const manualLayers = [];
      for (let i = 0; i < lg.layers.length; i++) {
        if (/^c\d*$/.test(('' + lg.layers[i].name).trim().toLowerCase())) manualLayers.push(lg.layers[i]);
      }
      // Duplicate ONLY the manual c layers when present, else the whole LOGO group; show the copies
      // and flatten them to one raster layer. All by ID via batchPlay — reliable where the DOM
      // duplication is not.
      const srcIds = manualLayers.length ? manualLayers.map((L) => L.id) : [lg.id];
      const sel = [{ _obj: 'select', _target: [{ _ref: 'layer', _id: srcIds[0] }], makeVisible: false }];
      for (let k = 1; k < srcIds.length; k++) {
        sel.push({ _obj: 'select', _target: [{ _ref: 'layer', _id: srcIds[k] }], selectionModifier: { _enum: 'selectionModifierType', _value: 'addToSelection' }, makeVisible: false });
      }
      await action.batchPlay(sel, {});
      await action.batchPlay([{ _obj: 'duplicate', _target: [{ _ref: 'layer', _enum: 'ordinal', _value: 'targetEnum' }], version: 5 }], {});
      await action.batchPlay([{ _obj: 'show', null: [{ _ref: 'layer', _enum: 'ordinal', _value: 'targetEnum' }] }], {});
      // Merge ONLY when the selection is a group or multiple layers — with a SINGLE flat layer
      // selected, Photoshop's merge command acts as MERGE DOWN and fuses the copy into the layer
      // below it (the original!). A single duplicated art layer already IS the copy.
      const needMerge = srcIds.length > 1 || manualLayers.length === 0 || isGroup(manualLayers[0]);
      if (needMerge) await action.batchPlay([{ _obj: 'mergeLayersNew' }], {});
      const copy = doc.activeLayers && doc.activeLayers[0];
      if (!copy) throw new Error('duplicate/merge produced no layer');
      out.copyId = copy.id;
      copy.name = TMP_LOGO;
      // Copies of manual c layers land INSIDE the LOGO group — move the copy out (above the group),
      // or hiding LOGO below would hide the copy with it.
      if (manualLayers.length) await copy.move(lg, constants.ElementPlacement.PLACEBEFORE);
      lg.visible = false;
      if (!copy.visible) copy.visible = true;
      const b = copy.bounds;
      if (!(b.right - b.left > 0 && b.bottom - b.top > 0)) {
        await deleteById(doc, out.copyId); out.copyId = null; lg.visible = true;
        out.warning = (manualLayers.length ? 'The manual collection logo (c/c1/c2…) has' : 'The LOGO group has') +
          ' no visible content; the Collection poster was exported without a logo.';
        return;
      }
      const H = doc.height;
      const targetBottom = H - COLLECTION_BOTTOM * H;
      const allowedH = (LOGO_TOP_LIMIT - COLLECTION_BOTTOM) * H;
      const curH = b.bottom - b.top;
      if (curH > allowedH) {
        const pct = (allowedH / curH) * 100;
        await copy.scale(pct, pct, constants.AnchorPosition.BOTTOMCENTER);
        const b2 = copy.bounds;   // re-measure and snap to the ruler, keeping the original center
        await copy.translate(((b.left + b.right) / 2) - ((b2.left + b2.right) / 2), targetBottom - b2.bottom);
        out.note = 'Collection: the logo is ' + Math.round(curH) + 'px tall but only ' + Math.round(allowedH) +
          'px fits above the collection ruler; scaled a temporary copy to ' + (Math.round(pct * 10) / 10) + '% to fit.';
      } else {
        await copy.translate(0, targetBottom - b.bottom);
      }
      out.placed = true;
    } catch (e) {
      try { if (out.copyId != null) await deleteById(doc, out.copyId); } catch (_) {}
      try { if (lg) lg.visible = true; } catch (_) {}
      out.placed = false; out.copyId = null;
      out.warning = 'Collection logo placement failed (' + (e && e.message ? e.message : e) + '); exported without repositioning the logo.';
    }
  }, { commandName: 'Place collection logo' });
  return out;
}

// Delete a layer by ID via batchPlay — reliable regardless of DOM object state.
async function deleteById(doc, id) {
  await action.batchPlay([{ _obj: 'delete', _target: [{ _ref: 'layer', _id: id }] }], {});
}

// Delete the temporary merged copy (by the ID captured at placement) and re-show the LOGO group.
// Also sweeps any __PFLTMPLOGO__ orphans left by older runs. Best-effort: never throws.
async function removeCollectionLogo(doc, constants, placed) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  await core.executeAsModal(async () => {
    try { if (placed && placed.copyId != null) await deleteById(doc, placed.copyId); } catch (_) {}
    try {
      const sweep = async (layers) => {
        for (let i = layers.length - 1; i >= 0; i--) {
          const L = layers[i];
          if (L.name === TMP_LOGO) { await deleteById(doc, L.id); continue; }
          if (isGroup(L)) await sweep(L.layers);
        }
      };
      await sweep(doc.layers);
      const lg = await findLogoGroup(doc, constants);
      if (lg && !lg.visible) lg.visible = true;
    } catch (_) {}
  }, { commandName: 'Remove collection logo' });
}

// Batch prep (jsx parity): manual collection logos (LOGO children named c/c1/c2…) stay hidden for
// every non-collection export — they only appear during the Collection placement itself. Without
// this, a visible c logo renders alongside the normal logo on main/season exports.
async function hideManualCollectionLogos(doc, constants) {
  await core.executeAsModal(async () => {
    const lg = await findLogoGroup(doc, constants);
    if (!lg) return;
    for (let i = 0; i < lg.layers.length; i++) {
      if (/^c\d*$/.test(('' + lg.layers[i].name).trim().toLowerCase()) && lg.layers[i].visible) {
        lg.layers[i].visible = false;
      }
    }
  }, { commandName: 'Hide manual collection logos' });
}

// Gradient safety net: force every root-level GRADIENT group (and its whole subtree) visible, and
// LEAVE it on — a nudged-off gradient otherwise ruins a whole batch. Returns true if anything changed.
async function forceGradientVisible(doc, constants) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  let changed = false;
  await core.executeAsModal(async () => {
    const fv = (node) => {
      if (!node.visible) { node.visible = true; changed = true; }
      if (isGroup(node)) for (let j = 0; j < node.layers.length; j++) fv(node.layers[j]);
    };
    for (let i = 0; i < doc.layers.length; i++) {
      const L = doc.layers[i];
      if (isGroup(L) && /^\s*gradients?\s*$/i.test(L.name)) fv(L);
    }
  }, { commandName: 'Force gradient visible' });
  return changed;
}

// Export the LOGO group as a trimmed transparent PNG into `folder` (a UXP Folder entry).
// Works on a DUPLICATE so the open doc is untouched: isolate the chain down to the LOGO group,
// alpha-trim the duplicate (Image > Trim, transparent), save a PNG copy, close the duplicate.
async function exportLogoPng(doc, constants, folder, filename) {
  let result = { ok: false, reason: 'error' };
  await core.executeAsModal(async () => {
    const dup = await doc.duplicate();
    try {
      const chain = await findLogoPath(dup, constants);
      const logo = chain && layerAtPath(dup, chain);
      if (!logo) { result = { ok: false, reason: 'no-logo' }; return; }
      let cont = dup.layers;   // hide non-chain siblings at every level; keep the chain visible
      for (let k = 0; k < chain.length; k++) {
        for (let i = 0; i < cont.length; i++) cont[i].visible = (i === chain[k]);
        cont = cont[chain[k]].layers;
      }
      const b = logo.bounds;
      if (!(b.right - b.left > 0 && b.bottom - b.top > 0)) { result = { ok: false, reason: 'empty' }; return; }
      await dup.trim(constants.TrimType.TRANSPARENT);
      const file = await writeViaTemp(folder, filename, (tmp) => dup.saveAs.png(tmp, {}, true));
      result = { ok: true, filename, folderName: folder.name, entry: file };   // entry: remote docs upload it
    } finally {
      await dup.closeWithoutSaving();
    }
  }, { commandName: 'Export logo PNG' });
  return result;
}

// ---- Square Art crop ------------------------------------------------------
// Isolate the POSTER group's topmost visible child on `dup` (hide everything else).
// Returns '' on success, else 'noposter' / 'empty' — fail rather than guess at chrome.
function isolatePosterArt(dup, constants) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  const top = dup.layers;
  let pi = -1;
  for (let i = 0; i < top.length; i++) {
    if (isGroup(top[i]) && /^\s*poster\s*$/i.test(top[i].name)) { pi = i; break; }
  }
  if (pi < 0) return 'noposter';
  for (let i = 0; i < top.length; i++) top[i].visible = (i === pi);
  const kids = top[pi].layers;
  let ai = -1;
  for (let i = 0; i < kids.length; i++) { if (kids[i].visible) { ai = i; break; } }
  if (ai < 0) return 'empty';
  for (let i = 0; i < kids.length; i++) kids[i].visible = (i === ai);
  return '';
}

// Create the crop doc: duplicate, isolate the POSTER art, trim to its pixels. Leaves the
// duplicate OPEN as the active tab — the caller closes it via closeCropDoc. If any of that fails,
// the duplicate is closed, so no stray copy of the PSD stays open.
async function makeCropDoc(doc, constants) {
  let result = { ok: false, reason: 'error' };
  await core.executeAsModal(async () => {
    const dup = await doc.duplicate();
    try {
      const iso = isolatePosterArt(dup, constants);
      if (iso) { await dup.closeWithoutSaving(); result = { ok: false, reason: iso }; return; }
      await dup.trim(constants.TrimType.TRANSPARENT);
      result = { ok: true, doc: dup, width: Math.round(dup.width), height: Math.round(dup.height) };
    } catch (e) {
      try { await dup.closeWithoutSaving(); } catch (_) {}
      throw e;
    }
  }, { commandName: 'Open Square Art crop' });
  return result;
}

// Place a square selection on `dup` (activating it) and switch to the rectangular marquee.
async function setSquareSelection(dup, x, y, side) {
  await core.executeAsModal(async () => {
    app.activeDocument = dup;
    await action.batchPlay([{
      _obj: 'set',
      _target: [{ _ref: 'channel', _property: 'selection' }],
      to: { _obj: 'rectangle',
        top: { _unit: 'pixelsUnit', _value: y }, left: { _unit: 'pixelsUnit', _value: x },
        bottom: { _unit: 'pixelsUnit', _value: y + side }, right: { _unit: 'pixelsUnit', _value: x + side } },
    }], {});
    await action.batchPlay([{ _obj: 'select', _target: [{ _ref: 'marqueeRectTool' }] }], {});
  }, { commandName: 'Place crop selection' });
}

// Read `dup`'s selection bounds (plain px numbers); null when there is no selection.
async function readSelectionBounds(dup) {
  try {
    const r = await action.batchPlay([{ _obj: 'get', _target: [{ _property: 'selection' }, { _ref: 'document', _id: dup.id }] }], {});
    const s = r && r[0] && r[0].selection;
    if (!s || s.left === undefined) return null;
    return { l: s.left._value, t: s.top._value, r: s.right._value, b: s.bottom._value,
             cw: Math.round(dup.width), ch: Math.round(dup.height) };
  } catch (_) { return null; }
}

// Fire-and-forget tool switch ('marqueeRectTool' / 'moveTool').
async function selectTool(toolRef) {
  try {
    await core.executeAsModal(async () => {
      await action.batchPlay([{ _obj: 'select', _target: [{ _ref: toolRef }] }], {});
    }, { commandName: 'Switch tool' });
  } catch (_) {}
}

// Crop `dup` to the square, upscale to wantSide when the art capped the selection, save JPG.
async function cropSaveSquare(dup, folder, filename, rect, wantSide) {
  let result = { ok: false, reason: 'error' };
  await core.executeAsModal(async () => {
    await dup.crop({ left: rect.x, top: rect.y, right: rect.x + rect.side, bottom: rect.y + rect.side });
    const up = wantSide && wantSide > rect.side;
    if (up) await dup.resizeImage(wantSide, wantSide);
    const file = await writeViaTemp(folder, filename, (tmp) => dup.saveAs.jpg(tmp, { quality: JPG_QUALITY }, true));
    result = { ok: true, filename, folderName: folder.name, entry: file, side: up ? wantSide : rect.side, srcSide: rect.side };
  }, { commandName: 'Crop square art' });
  return result;
}

// Export each SELECTED layer alone as a trimmed JPG (duplicate → only that layer's chain
// visible → trim → save → close). nameFor(layerName) supplies each filename.
// Returns { ok, files: [{ filename, entry }], folderName } or { ok:false, reason:'no-layer' }.
async function exportSelectedLayersJpg(doc, constants, folder, nameFor) {
  const isGroup = (L) => L.kind === constants.LayerKind.GROUP;
  const layers = (doc.activeLayers || []).slice();
  if (!layers.length) return { ok: false, reason: 'no-layer' };
  // Compare by id — UXP hands out a FRESH wrapper object on every collection access, so
  // object identity (===) never matches between doc.layers and doc.activeLayers.
  const pathOf = (target) => {
    const walk = (ls, pre) => {
      for (let i = 0; i < ls.length; i++) {
        const L = ls[i], here = pre.concat(i);
        if (L.id === target.id) return here;
        if (isGroup(L)) { const f = walk(L.layers, here); if (f) return f; }
      }
      return null;
    };
    return walk(doc.layers, []);
  };
  const files = [];
  const used = {};   // two plain-named layers in one run map to the same name — number the extras
  const dedupe = (filename) => {
    if (!used[filename]) { used[filename] = true; return filename; }
    const stem = filename.replace(/\.jpg$/i, '');
    let k = 2;
    while (used[stem + ' (' + k + ').jpg']) k++;
    const out = stem + ' (' + k + ').jpg';
    used[out] = true;
    return out;
  };
  await core.executeAsModal(async () => {
    for (const target of layers) {
      const path = pathOf(target);
      if (!path) continue;
      const dup = await doc.duplicate();
      try {
        let cont = dup.layers;
        for (let k = 0; k < path.length; k++) {
          for (let i = 0; i < cont.length; i++) cont[i].visible = (i === path[k]);
          if (k < path.length - 1) cont = cont[path[k]].layers;
        }
        await dup.trim(constants.TrimType.TRANSPARENT);
        const filename = dedupe(nameFor(target.name));
        const file = await writeViaTemp(folder, filename, (tmp) => dup.saveAs.jpg(tmp, { quality: JPG_QUALITY }, true));
        files.push({ filename, entry: file });
      } finally {
        await dup.closeWithoutSaving();
      }
    }
  }, { commandName: 'Export poster JPG' });
  return files.length ? { ok: true, files, folderName: folder.name } : { ok: false, reason: 'no-layer' };
}

// Close the crop doc and reactivate the home document.
async function closeCropDoc(dup, homeDoc) {
  try {
    await core.executeAsModal(async () => {
      await dup.closeWithoutSaving();
      if (homeDoc) { try { app.activeDocument = homeDoc; } catch (_) {} }
    }, { commandName: 'Close Square Art crop' });
  } catch (_) {}
}

// ---- Layer tree in one call --------------------------------------------------------------
// Walking the DOM costs a host round-trip per property read (about 1,500 per refresh on a 60-season
// template, and every click used to trigger one). multiGet fetches name / visibility / id / section
// marker for every layer in ONE call; treeFromList turns it back into the tree the model code reads.
// index 1 skips a Background layer (index 0 when present), which no template toggles anyway.
async function readLayerTree(doc, constants) {
  const r = await action.batchPlay([{
    _obj: 'multiGet',
    _target: { _ref: [{ _ref: 'document', _id: doc.id }] },
    extendedReference: [['name', 'visible', 'layerID', 'layerSection'], { _obj: 'layer', index: 1, count: -1 }],
    options: { failOnMissingProperty: false, failOnMissingElement: false },
  }], {});
  const list = r && r[0] && r[0].list;
  if (!Array.isArray(list)) throw new Error('multiGet returned no layer list');
  return treeFromList(list, constants);
}

// Show / hide layers by id: one batchPlay in one modal scope, instead of a DOM write per layer.
async function setLayersVisible(pairs, commandName) {
  if (!pairs.length) return;
  await core.executeAsModal(async () => {
    await action.batchPlay(pairs.map((p) => ({ _obj: p.v ? 'show' : 'hide', null: [{ _ref: 'layer', _id: p.id }] })), {});
  }, { commandName: commandName || 'Toggle poster layers' });
}

// ---- Edit in tab / Send back: clean up one poster or logo layer in a document of its own ----
// Edit duplicates the layer into a new document (Layer > Duplicate Layer > New), reveals all of it and
// crops to it, so the tab holds the layer's full pixels, off-canvas overhang included. Send back stamps
// what the tab shows into one layer and brings it into the PSD as ONE history step, rolled back whole
// if any part fails:
//  - a logo tab replaces its layer
//  - a poster tab that is now see-through (G.LOGO_SHARE: a logo cut out of the poster) becomes the new
//    top layer of LOGO, placed like Place Logo, earlier logos hidden; the poster is left as it was
//  - any other poster tab replaces its layer
// A replacement keeps the original's name, visibility, opacity, blend mode and place in the stack.
// Unchanged size lands on the original's exact spot; a resized one is placed like Fit Poster / Place Logo.
const SENT_BACK = '__PFLSENTBACK__';   // the arriving copy's name until it takes its real one

// Scale `layer` (now `w` wide) about its centre to res.width, then move its top-left to res.left / res.top.
async function fitTo(layer, res, w, constants) {
  const sc = (res.width / w) * 100;
  await layer.scale(sc, sc, constants.AnchorPosition.MIDDLECENTER);
  const b = layer.bounds;   // re-measure after the resize
  await layer.translate(res.left - b.left, res.top - b.top);
}

// Open `layer` of `doc` in a tab of its own, named `name`. Returns the tab's document.
async function openLayerInTab(doc, layer, name) {
  let tab = null;
  await core.executeAsModal(async () => {
    app.activeDocument = doc;
    await action.batchPlay([
      { _obj: 'select', _target: [{ _ref: 'layer', _id: layer.id }], makeVisible: false },
      { _obj: 'make', _target: [{ _ref: 'document' }], name, using: { _ref: 'layer', _enum: 'ordinal', _value: 'targetEnum' }, version: 5 },
    ], {});
    tab = app.activeDocument;
    if (!tab || tab.id === doc.id) { tab = null; throw new Error('Photoshop did not open a new tab'); }   // never crop the PSD itself
    try {
      const copy = tab.layers[0];
      copy.visible = true;   // a hidden season layer arrives hidden
      await action.batchPlay([{ _obj: 'revealAll' }], {});
      const b = copy.bounds;
      await tab.crop({ left: b.left, top: b.top, right: b.right, bottom: b.bottom });
    } catch (e) {   // no half-made tab stays open
      try { await tab.closeWithoutSaving(); app.activeDocument = doc; } catch (_) {}
      throw e;
    }
  }, { commandName: 'Edit in tab' });
  return tab;
}

// Whether a layer still exists, asked by id in one call (a DOM walk costs a host round trip per layer).
async function layerExists(docId, id) {
  try {
    const r = await action.batchPlay([{ _obj: 'get', _target: [{ _property: 'layerID' }, { _ref: 'layer', _id: id }, { _ref: 'document', _id: docId }] }], {});
    return !!(r && r[0] && r[0].layerID === id);
  } catch (_) { return false; }
}

// How much of `doc`'s canvas is see-through in one layer (G.seeThroughShare), or null when Photoshop
// can't hand over the pixels. Runs inside the caller's modal scope.
async function seeThroughShare(doc, layerId) {
  const imaging = require('photoshop').imaging;
  if (!imaging || !imaging.getPixels) return null;
  const W = Math.round(doc.width), H = Math.round(doc.height);
  let img = null;
  try {
    const res = await imaging.getPixels({ documentID: doc.id, layerID: layerId, componentSize: 8, applyAlpha: false, sourceBounds: { left: 0, top: 0, right: W, bottom: H } });
    img = res && res.imageData;
    if (!img) return null;
    const sb = res.sourceBounds || { left: 0, top: 0, right: W, bottom: H };   // trimmed to the part that holds pixels
    const data = img.hasAlpha ? await img.getData({ chunky: true }) : null;
    return G.seeThroughShare(data, img.width, img.height, img.components, !!img.hasAlpha, sb.right - sb.left, sb.bottom - sb.top, W, H);
  } catch (_) { return null; }
  finally { try { if (img) img.dispose(); } catch (_) {} }
}

function findLayerNamed(layers, name) {
  for (let i = 0; i < layers.length; i++) {
    if (layers[i].name === name) return layers[i];
    const f = layers[i].layers ? findLayerNamed(layers[i].layers, name) : null;
    if (f) return f;
  }
  return null;
}

// ctx, recorded at Edit: { srcDocId, layerId, layer, kind: 'poster' | 'logo', tag, logoName, left, top, w, h }.
// Returns { ok: true, mode: 'poster' | 'logo' | 'newLogo', refit, share, hidOld }, or
// { ok: false, reason: 'psd-closed' | 'layer-gone' | 'no-logo-group' }. On any failure the tab stays open.
async function sendBackFromTab(tab, ctx, constants) {
  const src = Array.from(app.documents || []).find((d) => d.id === ctx.srcDocId);
  if (!src) return { ok: false, reason: 'psd-closed' };
  const old = (await layerExists(src.id, ctx.layerId)) ? ctx.layer : null;
  let out = null;
  await core.executeAsModal(async (ec) => {
    app.activeDocument = tab;
    // Stamp Visible: what the tab shows, as one new layer. A tab with a single layer can't be stamped: that layer is it.
    let stampId, stamped = false;
    try {
      await action.batchPlay([{ _obj: 'mergeVisible', duplicate: true }], {});
      stampId = tab.activeLayers[0].id;
      stamped = true;
    } catch (e) {
      const shown = Array.from(tab.layers).filter((L) => L.visible);
      if (shown.length !== 1) throw e;
      stampId = shown[0].id;
    }
    let mode = ctx.kind, share = null;
    if (mode === 'poster') {
      share = await seeThroughShare(tab, stampId);
      if (share !== null && share >= G.LOGO_SHARE) mode = 'newLogo';
    }
    const logoGrp = mode === 'newLogo' ? await findLogoGroup(src, constants) : null;
    const stop = mode === 'newLogo' ? (logoGrp ? '' : 'no-logo-group') : (old ? '' : 'layer-gone');
    if (stop) {
      if (stamped) { try { await action.batchPlay([{ _obj: 'delete', _target: [{ _ref: 'layer', _id: stampId }] }], {}); } catch (_) {} }
      out = { ok: false, reason: stop };
      return;
    }
    // Everything that touches the PSD, the arriving copy included, is one history step: kept when it
    // all worked, rolled back otherwise.
    const step = await ec.hostControl.suspendHistory({ documentID: src.id, name: mode === 'newLogo' ? 'Send back as logo' : 'Send back ' + ctx.tag });
    let done = false;
    try {
      await action.batchPlay([
        { _obj: 'select', _target: [{ _ref: 'layer', _id: stampId }], makeVisible: false },
        { _obj: 'duplicate', _target: [{ _ref: 'layer', _enum: 'ordinal', _value: 'targetEnum' }], to: { _ref: 'document', _id: src.id }, name: SENT_BACK, version: 5 },
      ], {});
      app.activeDocument = src;
      let copy = null;   // selecting it by name makes it the active layer
      try {
        await action.batchPlay([{ _obj: 'select', _target: [{ _ref: 'layer', _name: SENT_BACK }], makeVisible: false }], {});
        const active = src.activeLayers[0];
        if (active && active.name === SENT_BACK) copy = active;
      } catch (_) {}
      if (!copy) copy = findLayerNamed(src.layers, SENT_BACK);
      if (!copy) throw new Error('the cleaned layer did not arrive in the PSD');
      const b = copy.bounds, w = b.right - b.left, h = b.bottom - b.top;
      if (mode === 'newLogo') {
        const logos = Array.from(logoGrp.layers || []);
        if (logos.length) await copy.move(logos[0], constants.ElementPlacement.PLACEBEFORE);
        else await copy.move(logoGrp, constants.ElementPlacement.PLACEINSIDE);
        let hidOld = 0;
        for (const K of logos) { if (K.visible) { K.visible = false; hidOld++; } }
        copy.name = ctx.logoName;
        copy.visible = true;
        await fitTo(copy, G.computeLogoGeometry(w, h, src.width, src.height, LOGO_DENSITY), w, constants);
        if (old) {   // the poster stays selected, so the next Edit opens it again (to clean its lettering)
          try { await action.batchPlay([{ _obj: 'select', _target: [{ _ref: 'layer', _id: old.id }], makeVisible: false }], {}); } catch (_) {}
        }
        out = { ok: true, mode, share, hidOld };
      } else {
        const wasVisible = old.visible;
        await copy.move(old, constants.ElementPlacement.PLACEBEFORE);
        copy.name = old.name;
        try { copy.opacity = old.opacity; copy.blendMode = old.blendMode; } catch (_) {}
        const refit = Math.abs(w - ctx.w) >= 1 || Math.abs(h - ctx.h) >= 1;
        if (refit) {
          await fitTo(copy, mode === 'logo'
            ? G.computeLogoGeometry(w, h, src.width, src.height, LOGO_DENSITY)
            : G.computePosterFitGeometry(w, h, src.width, bottomGuideY(src, constants)), w, constants);
        } else {
          await copy.translate(ctx.left - b.left, ctx.top - b.top);
        }
        copy.visible = wasVisible;
        await deleteById(src, old.id);
        out = { ok: true, mode, refit };
      }
      done = true;
    } finally {
      await ec.hostControl.resumeHistory(step, done);   // false rolls the PSD back to before Send back
      if (!done) {   // and the arriving copy never stays behind
        try { await action.batchPlay([{ _obj: 'delete', _target: [{ _ref: 'layer', _name: SENT_BACK }, { _ref: 'document', _id: src.id }] }], {}); } catch (_) {}
      }
    }
    await tab.closeWithoutSaving();
    app.activeDocument = src;
  }, { commandName: 'Send back' });
  return out;
}

// Close an edit tab without sending it back, and return to its PSD when that is still open.
async function closeEditTab(tab, srcDocId) {
  await core.executeAsModal(async () => {
    await tab.closeWithoutSaving();
    const src = Array.from(app.documents || []).find((d) => d.id === srcDocId);
    if (src) app.activeDocument = src;
  }, { commandName: 'Close edit tab' });
}

// Save BG's last step: close the tab, return to its PSD and take the layer it was opened from out of it
// (one history step). Returns whether the layer was still there to remove.
async function closeEditTabAndRemove(tab, srcDocId, layerId) {
  let removed = false;
  await core.executeAsModal(async () => {
    await tab.closeWithoutSaving();
    const src = Array.from(app.documents || []).find((d) => d.id === srcDocId);
    if (!src) return;
    app.activeDocument = src;
    if (await layerExists(src.id, layerId)) { await deleteById(src, layerId); removed = true; }
  }, { commandName: 'Remove backdrop layer' });
  return removed;
}

module.exports = {
  readLayerTree, setLayersVisible, openLayerInTab, sendBackFromTab, closeEditTab, closeEditTabAndRemove, findLogoGroup,
  placeSelected, trim, exportLogoPng, placeCollectionLogo, removeCollectionLogo, forceGradientVisible,
  hideManualCollectionLogos, LOGO_DENSITY, makeCropDoc, setSquareSelection, readSelectionBounds,
  selectTool, cropSaveSquare, closeCropDoc, exportSelectedLayersJpg, checkCoverage, TMP_LOGO,
};
