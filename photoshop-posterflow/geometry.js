// Pixel-exact placement math — mirror of compute_logo_geometry / compute_poster_fit_geometry in
// backend/api/maker_tools.py (pinned by backend tests). Ported verbatim from the Photopea panel.
// rnd = round-half-to-even (matches Python round()), so results are pixel-identical to the PSD export.
'use strict';

const rnd = (x) => { const f = Math.floor(x); return (Math.abs(x - f - 0.5) < 1e-9) ? (f % 2 === 0 ? f : f + 1) : Math.round(x); };

const computeLogoGeometry = (srcW, srcH, cw, ch, density) => {
  const logoBottom = rnd(ch * (1352.13 / 1500));
  const maxLogoTop = rnd(ch * (1100.0 / 1500));
  const maxLogoH = logoBottom - maxLogoTop;
  const maxLogoW = rnd(cw * (800.0 / 1000));
  const logoPx = srcW * srcH;
  const ceiling = logoPx < 200000 ? 0.85 : (logoPx > 1500000 ? 0.93 : 0.84);
  const projHAtMax = srcH * (maxLogoW / srcW);
  const refH = cw * (90.0 / 1000);
  const targetRatio = ceiling * Math.pow(refH / Math.max(projHAtMax, refH), 0.40);
  const densityFloor = 0.58 + Math.max(0.0, density - 0.30) * 0.10;
  let targetW = rnd(cw * Math.max(densityFloor, Math.min(ceiling, targetRatio)));
  const wideThreshold = rnd(cw * (600.0 / 1000));
  let maxH = targetW > wideThreshold ? rnd(ch * (225.0 / 1500)) : maxLogoH;
  if (density < 0.30) {                    // sparse → size up
    const t = (0.30 - density) / 0.30, mult = 1.0 + t * 0.15;
    targetW = Math.min(rnd(targetW * mult), maxLogoW);
    maxH = Math.min(rnd(maxH * mult), maxLogoH);
  } else if (density > 0.60) {              // dense → tighter
    const t = (density - 0.60) / 0.40, wMult = 1.0 - t * 0.10;
    targetW = rnd(targetW * wMult);
    maxH = rnd(ch * (225.0 / 1500) * (1.0 - t * 0.55));
  }
  let scale = targetW / srcW;
  if (srcH * scale > maxH) scale = maxH / srcH;
  if (srcW * scale > maxLogoW) scale = maxLogoW / srcW;
  const w = rnd(srcW * scale), h = rnd(srcH * scale);
  return { width: w, height: h, left: Math.floor((cw - w) / 2), top: logoBottom - h };
};

// Cover-fit into the bordered box (25px sides, top y=25, bottom at bottomY — the template's
// lowest horizontal guide, or canvas − 25 when none): the larger width/height ratio wins, so the
// art always reaches the bottom bound; centered horizontally (may overhang the sides).
const computePosterFitGeometry = (srcW, srcH, cw, bottomY) => {
  const border = 25, boxW = Math.max(1, cw - border * 2), boxH = Math.max(1, bottomY - border);
  const scale = Math.max(boxW / srcW, boxH / srcH);
  const w = Math.max(1, rnd(srcW * scale)), h = Math.max(1, rnd(srcH * scale));
  return { width: w, height: h, left: Math.floor((cw - w) / 2), top: border };
};

// Square Art edge keeper. Photoshop does NOT clip a selection dragged past the canvas edge: it keeps
// its full size and sits partly outside (measured l=118 r=1076 on a 958-wide canvas, still 958 square),
// and Crop's x/y clamp would then save a different region than the one on screen. Returns the square to
// re-select, or null when there is nothing to fix. Clamping the TOP-LEFT pins the square to the edge it
// crossed and leaves the other axis where the user put it. Non-square marquees are left to Crop's snap.
const keepSquareInside = (s) => {
  const w = Math.round(s.r - s.l), h = Math.round(s.b - s.t);
  if (Math.abs(w - h) > 2) return null;
  if (s.l >= 0 && s.t >= 0 && s.r <= s.cw && s.b <= s.ch) return null;
  const side = Math.min(w, s.cw, s.ch);
  const clamp = (v, hi) => Math.max(0, Math.min(Math.round(v), hi));
  return { side, x: clamp(s.l, s.cw - side), y: clamp(s.t, s.ch - side) };
};

// The square worth restoring after a stray deselect: a square (±2 px) sitting fully on the canvas, else null.
const squareOnCanvas = (s) => {
  const w = Math.round(s.r - s.l), h = Math.round(s.b - s.t);
  if (Math.abs(w - h) > 2 || s.l < 0 || s.t < 0 || s.r > s.cw || s.b > s.ch) return null;
  return { x: Math.round(s.l), y: Math.round(s.t), side: Math.min(w, h) };
};

// ---- Coverage: the box a poster must fill, and what a layer leaves blank in it ----
// The box Fit Poster covers: inside the 25 px border, from the top border down to the bottom line.
const coverageBox = (cw, bottomY) => ({ left: 25, top: 25, right: Math.round(cw) - 25, bottom: Math.floor(bottomY) });
// Where a layer's bounds fall short of the box by a pixel or more, worst first: [{ edge, px }].
const coverageGaps = (b, box) => [
  { edge: 'bottom', px: box.bottom - b.bottom }, { edge: 'top', px: b.top - box.top },
  { edge: 'left', px: b.left - box.left }, { edge: 'right', px: box.right - b.right },
].filter((g) => g.px >= 1).sort((a, c) => c.px - a.px);
// The gaps as text: "~75 px at the left edge", and for the bottom "~40 px above the bottom guide".
const coverageGapText = (gaps) => gaps.map((g) => '~' + Math.round(g.px) + ' px ' + (g.edge === 'bottom' ? 'above the bottom guide' : 'at the ' + g.edge + ' edge')).join(', ');
// Alpha below this (90 %) counts as blank; the slightly soft edge pixels a transform leaves pass.
const COVER_ALPHA = 230;
// See-through pixels in chunky 8-bit pixel data whose last component is alpha: { count, x0, x1, y0, y1 }.
const blankPixels = (data, w, h, comps, opaque = COVER_ALPHA) => {
  let count = 0, x0 = w, x1 = -1, y0 = h, y1 = -1;
  for (let y = 0; y < h; y++) {
    let i = y * w * comps + comps - 1;
    for (let x = 0; x < w; x++, i += comps) {
      if (data[i] < opaque) { count++; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    }
  }
  return { count, x0, x1, y0, y1 };
};

// ---- Send back: is an edited poster tab now a cut-out logo? ----
// A poster covers its whole tab. With this much of the tab see-through, it holds a logo cut out of the poster.
const LOGO_SHARE = 0.02;
// The see-through share of a cw x ch canvas, from the pixels Photoshop handed over for one layer.
// `data` (w x h, alpha last of `comps`) covers the areaW x areaH part that holds pixels, perhaps scaled
// down; everything outside that part is see-through. No alpha channel = every pixel there is solid.
const seeThroughShare = (data, w, h, comps, hasAlpha, areaW, areaH, cw, ch) => {
  const n = w * h;
  let solid = n;
  if (hasAlpha) {
    solid = 0;
    for (let i = comps - 1; i < data.length; i += comps) if (data[i] >= COVER_ALPHA) solid++;
  }
  return Math.max(0, 1 - (n ? solid / n : 0) * (areaW * areaH) / (cw * ch));
};

module.exports = { rnd, computeLogoGeometry, computePosterFitGeometry, keepSquareInside, squareOnCanvas, coverageBox, coverageGaps, coverageGapText, blankPixels, COVER_ALPHA, LOGO_SHARE, seeThroughShare };
