// Scrub the metadata Photoshop's Save As bakes into every export: EXIF + XMP (edit history, document
// IDs, the template's text layers, the Content Credentials link), IPTC, the Photoshop resource block
// (quality stamp, thumbnail) and any C2PA segment. Pure byte walkers over the JPEG header segments /
// PNG chunks, so the encoded pixels are copied verbatim.
'use strict';

const JPEG_DROP = new Set([0xE1, 0xEB, 0xED]);   // APP1 (EXIF, XMP), APP11 (JUMBF/C2PA), APP13 (Photoshop IRB, IPTC)
const JFIF = Uint8Array.from([0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x01, 0x00, 0x48, 0x00, 0x48, 0x00, 0x00]);   // JFIF 1.01, 72 dpi
const PNG_SIG = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
const PNG_DROP = new Set(['tEXt', 'zTXt', 'iTXt', 'eXIf', 'tIME']);

const concat = (parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out.buffer;
};

// Returns the input untouched when it isn't a JPEG or the header is malformed.
function stripJpeg(buf) {
  const b = new Uint8Array(buf);
  if (b.length < 4 || b[0] !== 0xFF || b[1] !== 0xD8) return buf;
  const keep = [];
  let hasJfif = false, i = 2;
  while (i + 2 <= b.length && b[i] === 0xFF) {
    const m = b[i + 1];
    if (m === 0xFF) { i++; continue; }                                                               // fill byte
    if (m === 0xDA || m === 0xD9) break;                                                             // SOS / EOI: header done
    if (m === 0x01 || (m >= 0xD0 && m <= 0xD7)) { keep.push(b.subarray(i, i + 2)); i += 2; continue; }   // standalone markers
    if (i + 4 > b.length) return buf;
    const len = (b[i + 2] << 8) | b[i + 3];
    if (len < 2 || i + 2 + len > b.length) return buf;
    if (m === 0xE0) hasJfif = true;
    if (!JPEG_DROP.has(m)) keep.push(b.subarray(i, i + 2 + len));
    i += 2 + len;
  }
  return concat([b.subarray(0, 2), ...(hasJfif ? [] : [JFIF]), ...keep, b.subarray(i)]);
}

function stripPng(buf) {
  const b = new Uint8Array(buf);
  if (b.length < 8 || PNG_SIG.some((v, k) => b[k] !== v)) return buf;
  const keep = [b.subarray(0, 8)];
  let i = 8;
  while (i + 12 <= b.length) {
    const len = ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0;
    const type = String.fromCharCode(b[i + 4], b[i + 5], b[i + 6], b[i + 7]);
    const end = i + 12 + len;
    if (end > b.length) return buf;
    if (!PNG_DROP.has(type)) keep.push(b.subarray(i, end));
    i = end;
    if (type === 'IEND') break;
  }
  return concat(keep);
}

// Rewrite an exported file in place without its metadata; on failure the export stays as written.
async function scrubFile(entry) {
  const formats = require('uxp').storage.formats;
  try {
    const buf = await entry.read({ format: formats.binary });
    const out = /\.png$/i.test(entry.name) ? stripPng(buf) : stripJpeg(buf);
    if (out !== buf) await entry.write(out, { format: formats.binary });
  } catch (_) {}
}

module.exports = { stripJpeg, stripPng, scrubFile };
