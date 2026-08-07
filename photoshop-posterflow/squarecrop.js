// Square Art crop dialog — a vanilla-JS port of frontend/src/components/maker-tools/SquareCropModal.tsx
// (drag-to-move, drag-corner-to-resize, size presets, native-px scaling) shown in an HTML5 <dialog>
// so it isn't bound by the docked panel's small preferred size (styles.css: .crop-dialog).
'use strict';

const MIN_OUTPUT = 500;   // square art must be at least 500x500 native px
const PRESETS = [500, 1000, 1500, 2000, 2500, 3000];

// Opens the dialog over `imageUrl` (a PREVIEW image, possibly downscaled from the true poster size).
// `nativeW`/`nativeH` are the poster's TRUE full-resolution dimensions — the crop rect passed to
// onSave() is always expressed in those native px, scaled up from whatever size the preview happens
// to render at, so the actual crop always runs against the full-resolution document.
// onSave(cropRectNative) must return a Promise; the dialog shows "Saving…" and only closes once it
// resolves, staying open with an error message on rejection so the user can retry.
function openSquareCropDialog({ imageUrl, nativeW, nativeH, title, onSave, onCancel }) {
  const dlg = document.createElement('dialog');
  dlg.className = 'crop-dialog';
  dlg.innerHTML =
    '<div class="crop-dialog__header"><span>Crop to square art' + (title ? ' — ' + escapeHtml(title) : '') + '</span>' +
    '<button type="button" class="crop-dialog__close">×</button></div>' +
    '<div class="crop-dialog__body">' +
      '<div class="crop-col">' +
        '<div class="crop-img-wrap"><img draggable="false" alt="" /></div>' +
        '<p class="crop-caption"></p>' +
      '</div>' +
      '<div class="crop-presets"><span class="crop-presets__label">PRESETS</span></div>' +
    '</div>' +
    '<div class="crop-dialog__footer">' +
      '<button type="button" class="btn cancel">Cancel</button>' +
      '<button type="button" class="btn primary save" disabled>Save as Square Art</button>' +
    '</div>';
  document.body.appendChild(dlg);

  const img = dlg.querySelector('.crop-img-wrap img');
  const wrap = dlg.querySelector('.crop-img-wrap');
  const caption = dlg.querySelector('.crop-caption');
  const presetsEl = dlg.querySelector('.crop-presets');
  const cancelBtn = dlg.querySelector('.cancel');
  const saveBtn = dlg.querySelector('.save');
  const closeBtn = dlg.querySelector('.crop-dialog__close');

  const maxNativeSquare = Math.min(nativeW, nativeH);
  const tooSmall = maxNativeSquare < MIN_OUTPUT;

  let box = null;   // { x, y, size } in DISPLAY px within wrap
  let drag = null;  // { mode, startX, startY, orig }
  let ro = null;

  const scale = () => nativeW / (img.clientWidth || 1);   // native px per display px (aspect preserved)

  function clampBox(b) {
    const w = img.clientWidth, h = img.clientHeight;
    const size = Math.max(1, Math.min(b.size, w, h));
    return { size, x: Math.max(0, Math.min(b.x, w - size)), y: Math.max(0, Math.min(b.y, h - size)) };
  }

  function updatePresetActive(nativeSize) {
    presetsEl.querySelectorAll('button.btn').forEach((b) => {
      b.classList.toggle('active', Number(b.dataset.size) === nativeSize);
    });
  }

  function renderBox() {
    wrap.querySelectorAll('.crop-box, .crop-badge').forEach((el) => el.remove());
    if (!box || tooSmall) { saveBtn.disabled = true; return; }
    const boxEl = document.createElement('div');
    boxEl.className = 'crop-box';
    boxEl.style.left = box.x + 'px'; boxEl.style.top = box.y + 'px';
    boxEl.style.width = box.size + 'px'; boxEl.style.height = box.size + 'px';
    const handleEl = document.createElement('div');
    handleEl.className = 'crop-box__handle';
    boxEl.appendChild(handleEl);
    wrap.appendChild(boxEl);

    const nativeSize = Math.round(box.size * scale());
    const badgeEl = document.createElement('div');
    badgeEl.className = 'crop-badge';
    badgeEl.style.left = (box.x + 4) + 'px'; badgeEl.style.top = (box.y + 4) + 'px';
    badgeEl.textContent = nativeSize + ' × ' + nativeSize;
    wrap.appendChild(badgeEl);

    boxEl.addEventListener('pointerdown', (e) => startDrag('move', e));
    handleEl.addEventListener('pointerdown', (e) => startDrag('resize', e));

    updatePresetActive(nativeSize);
    saveBtn.disabled = false;
  }

  function startDrag(mode, e) {
    e.preventDefault(); e.stopPropagation();
    drag = { mode, startX: e.clientX, startY: e.clientY, orig: Object.assign({}, box) };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', endDrag);
  }
  function onMove(e) {
    if (!drag) return;
    const w = img.clientWidth, h = img.clientHeight;
    const dx = e.clientX - drag.startX, dy = e.clientY - drag.startY;
    if (drag.mode === 'move') {
      box = clampBox({ x: drag.orig.x + dx, y: drag.orig.y + dy, size: drag.orig.size });
    } else {
      const sc = scale();
      const minSize = MIN_OUTPUT / sc;
      const maxSize = Math.min(w - drag.orig.x, h - drag.orig.y);
      const size = Math.min(maxSize, Math.max(Math.min(minSize, maxSize), drag.orig.size + Math.max(dx, dy)));
      box = { x: drag.orig.x, y: drag.orig.y, size };
    }
    renderBox();
  }
  function endDrag() {
    drag = null;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', endDrag);
  }

  function applyPreset(nativeSize) {
    const sc = scale();
    const w = img.clientWidth, h = img.clientHeight;
    const size = Math.min(nativeSize / sc, w, h);
    const cx = box ? box.x + box.size / 2 : w / 2;
    const cy = box ? box.y + box.size / 2 : h / 2;
    box = clampBox({ x: cx - size / 2, y: cy - size / 2, size });
    renderBox();
  }

  function buildPresets() {
    PRESETS.forEach((sz) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'btn'; b.dataset.size = String(sz);
      b.textContent = sz + ' × ' + sz;
      const disabled = tooSmall || sz > maxNativeSquare;
      b.disabled = disabled;
      b.title = disabled ? ('Poster too small for ' + sz + '×' + sz) : ('Set the crop to ' + sz + '×' + sz);
      b.addEventListener('click', () => applyPreset(sz));
      presetsEl.appendChild(b);
    });
  }

  function measure() {
    if (!img.clientWidth) return;
    if (tooSmall) {
      caption.textContent = 'This poster is only ' + nativeW + '×' + nativeH + 'px — too small to crop a ' +
        MIN_OUTPUT + '×' + MIN_OUTPUT + ' square.';
      saveBtn.disabled = true;
      return;
    }
    caption.textContent = 'Source poster: ' + nativeW + '×' + nativeH +
      'px · drag to move, drag the corner to resize (min ' + MIN_OUTPUT + '×' + MIN_OUTPUT + ').';
    box = box ? clampBox(box) : (() => {
      const size = Math.min(img.clientWidth, img.clientHeight);
      return { x: (img.clientWidth - size) / 2, y: (img.clientHeight - size) / 2, size };
    })();
    renderBox();
  }

  img.addEventListener('load', measure);
  img.src = imageUrl;
  buildPresets();
  try { ro = new ResizeObserver(() => measure()); ro.observe(img); } catch (_) {}

  function cleanup() {
    if (ro) ro.disconnect();
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', endDrag);
    dlg.close && dlg.open && dlg.close();
    dlg.remove();
  }
  function doCancel() { cleanup(); if (onCancel) onCancel(); }
  cancelBtn.addEventListener('click', doCancel);
  closeBtn.addEventListener('click', doCancel);
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); doCancel(); });   // Esc key

  saveBtn.addEventListener('click', async () => {
    if (!box || tooSmall) return;
    const sc = scale();
    const size = Math.max(1, Math.round(box.size * sc));
    const left = Math.max(0, Math.min(Math.round(box.x * sc), nativeW - size));
    const top = Math.max(0, Math.min(Math.round(box.y * sc), nativeH - size));
    const crop = { left, top, right: left + size, bottom: top + size };
    saveBtn.disabled = true; cancelBtn.disabled = true;
    const prevLabel = saveBtn.textContent; saveBtn.textContent = 'Saving…';
    try {
      await onSave(crop);
      cleanup();
    } catch (e) {
      saveBtn.textContent = prevLabel; saveBtn.disabled = false; cancelBtn.disabled = false;
      caption.textContent = 'Save failed: ' + (e && e.message ? e.message : e);
    }
  });

  if (typeof dlg.showModal === 'function') dlg.showModal();
  else dlg.setAttribute('open', '');   // defensive fallback if <dialog> modal support is ever missing
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

module.exports = { openSquareCropDialog };
