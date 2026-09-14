// Alt-click detection: UXP widget click events drop altKey on Windows, the pointer press before them doesn't.
'use strict';

function createAltTracker() {
  let pressAlt = false;   // altKey seen on the pointerdown / mousedown of the pending click
  const release = () => { pressAlt = false; };
  return {
    press(e) { pressAlt = pressAlt || !!(e && e.altKey); },
    release,
    // One-shot: true when this click or its press carried Alt, then cleared.
    consume(ev) {
      const alt = !!(ev && ev.altKey) || pressAlt;
      release();
      return alt;
    },
  };
}

module.exports = { createAltTracker };
