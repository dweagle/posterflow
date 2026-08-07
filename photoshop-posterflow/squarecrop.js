// Square Art crop dialog — a vanilla-JS port of frontend/src/components/maker-tools/SquareCropModal.tsx
// (drag-to-move, drag-corner-to-resize, size presets, native-px scaling) shown in an HTML5 <dialog>
// so it isn't bound by the docked panel's small preferred size (styles.css: .crop-dialog).
'use strict';

const MIN_OUTPUT = 500;   // square art must be at least 500x500 native px
// Posters here are never wider than 1000px (the canvas width), so larger presets would always be
// disabled dead weight — 950 covers a "Fit Poster inside border" export (950 = 1000 - 25px*2), 1000
// covers a full-bleed export (greyed out if the isolated art doesn't reach it, same as any preset).
const PRESETS = [500, 950, 1000];

// Opens the dialog over `imageUrl` (a PREVIEW image, possibly downscaled from the true poster size).
// `nativeW`/`nativeH` are the poster's TRUE full-resolution dimensions — the crop rect this resolves
// with is always expressed in those native px, scaled up from whatever size the preview happens to
// render at, so the actual crop always runs against the full-resolution document.
//
// This dialog is PURE UI — it never touches the Photoshop document/app API itself. It resolves with
// the chosen crop rect on Save, or null on Cancel/Esc/close, and closes IMMEDIATELY either way (no
// async work happens while it's open). The caller does the actual Photoshop work (duplicate, crop,
// save, folder pick, upload) AFTER awaiting this promise, once the dialog is fully gone —
// deliberately: while this modal had focus, Photoshop stopped reporting a usable app.activeDocument
// to code running elsewhere in the plugin (observed as "document with an id of undefined does not
// exist" whenever Photoshop-side work was attempted from inside the dialog's own Save handler, even
// with the document captured beforehand, and even with lockDocumentFocus set on the modal). Keeping
// the dialog Photoshop-API-free sidesteps that entirely rather than continuing to fight it.
// Returns a Promise<{left,top,right,bottom} | null>.
function openSquareCropDialog({ imageUrl, nativeW, nativeH, title }) {
  return new Promise((resolve) => {
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
    function doCancel() { cleanup(); resolve(null); }
    cancelBtn.addEventListener('click', doCancel);
    closeBtn.addEventListener('click', doCancel);
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); doCancel(); });   // Esc key

    saveBtn.addEventListener('click', () => {
      if (!box || tooSmall) return;
      const sc = scale();
      const size = Math.max(1, Math.round(box.size * sc));
      const left = Math.max(0, Math.min(Math.round(box.x * sc), nativeW - size));
      const top = Math.max(0, Math.min(Math.round(box.y * sc), nativeH - size));
      const crop = { left, top, right: left + size, bottom: top + size };
      cleanup();
      resolve(crop);
    });

    // UXP sizes <dialog> elements through its own uxpShowModal(options) call — CSS width/height on
    // the dialog itself is unreliable and can render as a tiny, content-shrunk box regardless of what
    // the stylesheet says. Prefer uxpShowModal when present; fall back to the standard showModal()
    // (older UXP) or a plain open attribute (defensive, if <dialog> support is ever missing outright).
    if (typeof dlg.uxpShowModal === 'function') {
      dlg.uxpShowModal({ title: 'Crop to square art', resize: 'both', size: { width: 860, height: 700 } });
    } else if (typeof dlg.showModal === 'function') {
      dlg.showModal();
    } else {
      dlg.setAttribute('open', '');
    }
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

module.exports = { openSquareCropDialog };
