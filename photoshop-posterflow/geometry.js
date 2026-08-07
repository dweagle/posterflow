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

// Cover-fit into the bordered box (canvas − 25px each side horizontally, top border down to
// bottomY vertically): scale by the LARGER of the width/height ratios so the poster always reaches
// bottomY, even if that means it overhangs the left/right border slightly (split evenly by the
// horizontal centering below). bottomY is the RESOLVED bottom bound — normally the document's own
// lowest horizontal guide (see findBottomGuideY in tools.js), not the raw canvas height.
const computePosterFitGeometry = (srcW, srcH, cw, bottomY) => {
  const border = 25, targetW = Math.max(1, cw - border * 2), targetH = Math.max(1, bottomY - border);
  const scale = Math.max(targetW / srcW, targetH / srcH);
  const w = Math.max(1, rnd(srcW * scale)), h = Math.max(1, rnd(srcH * scale));
  return { width: w, height: h, left: Math.floor((cw - w) / 2), top: border };
};

module.exports = { rnd, computeLogoGeometry, computePosterFitGeometry };
