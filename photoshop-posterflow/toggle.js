// Pure season/single/sequel mutual-exclusion logic — ported verbatim from the Photopea
// panel (frontend/public/photopea-plugin.html). NO Photoshop/UXP APIs here: every function
// takes the layer `model` + a target node and returns a `changes` list of { p, v } (path →
// visible). main.js applies each change to the real layer and updates the model. Keeping this
// UXP-free means it's testable in plain node and identical to the browser's behavior.
//
// model shape (built by model.js):
//   { singles: [{ lab, n, p, v }], seasons: [{ n, p, g, v, r }], sequels: [{ n, p, v }], seqGroup }
//   p = index path from doc.layers (e.g. [2,0,3]); r = role (main|decade|season|years|year|group|…)

'use strict';

const key = (p) => JSON.stringify(p);

const seqs = (model) => model.sequels || [];
const allNodes = (model) => model.singles.concat(model.seasons).concat(seqs(model));
const byRole = (model, r) => model.seasons.filter((n) => n.r === r);
const main = (model) => byRole(model, 'main')[0];

// Hide every sequel leaf so sequels stay mutually exclusive with seasons/singles (no-op on CL2K).
const seqOff = (model) => seqs(model).map((sq) => ({ p: sq.p, v: false }));

// Hide every year leaf + years group — years are mutually exclusive with seasons/singles/sequels.
const yearsOff = (model) =>
  byRole(model, 'years').map((yg) => ({ p: yg.p, v: false }))
    .concat(byRole(model, 'year').map((yn) => ({ p: yn.p, v: false })));
// Hide the whole SEQUEL group by its own path; fall back to the parent of the first sequel leaf.
const seqGroupOff = (model) =>
  model.seqGroup ? [{ p: model.seqGroup, v: false }]
    : (seqs(model).length ? [{ p: seqs(model)[0].p.slice(0, -1), v: false }] : []);

// Lowest-numbered season leaf (Season 1 when present) — the default when SEASONS is switched on.
const firstSeason = (model) => {
  const ss = byRole(model, 'season');
  if (!ss.length) return null;
  let best = ss[0], bestNum = Infinity;
  ss.forEach((s) => { const m = (s.n || '').match(/(\d+)/); const n = m ? parseInt(m[1], 10) : Infinity; if (n < bestNum) { bestNum = n; best = s; } });
  return best;
};

// ---- click behaviours: each returns a changes array [{ p, v }] --------------

const clickSeason = (model, S) => {
  if (S.v) return [{ p: S.p, v: false }];
  const parentKey = key(S.p.slice(0, -1));
  const changes = [{ p: S.p, v: true }];
  if (main(model)) changes.push({ p: main(model).p, v: true });
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: key(dn.p) === parentKey }));
  byRole(model, 'season').forEach((sn) => { if (sn !== S) changes.push({ p: sn.p, v: false }); });
  model.singles.forEach((si) => changes.push({ p: si.p, v: false }));
  changes.push(...yearsOff(model), ...seqOff(model), ...seqGroupOff(model));
  return changes;
};

const clickSingle = (model, SI) => {
  if (SI.v) return [{ p: SI.p, v: false }];
  const changes = [{ p: SI.p, v: true }];
  // Legacy PSDs nest singles inside SEASONS / wrapper groups — reveal every ancestor group too.
  for (let i = 1; i < SI.p.length; i++) changes.push({ p: SI.p.slice(0, i), v: true });
  model.singles.forEach((x) => { if (x !== SI) changes.push({ p: x.p, v: false }); });
  byRole(model, 'season').forEach((sn) => changes.push({ p: sn.p, v: false }));
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
  changes.push(...yearsOff(model), ...seqOff(model), ...seqGroupOff(model));
  return changes;
};

// MM2K only: sequel badges — mutually exclusive with each other, seasons, and singles.
const clickSequel = (model, SQ) => {
  if (SQ.v) return [{ p: SQ.p, v: false }, ...seqGroupOff(model)];
  const changes = [{ p: SQ.p, v: true }];
  for (let i = 1; i < SQ.p.length; i++) changes.push({ p: SQ.p.slice(0, i), v: true });  // reveal SEQUEL group
  seqs(model).forEach((x) => { if (x !== SQ) changes.push({ p: x.p, v: false }); });
  model.singles.forEach((si) => changes.push({ p: si.p, v: false }));
  if (main(model)) changes.push({ p: main(model).p, v: false });
  byRole(model, 'season').forEach((sn) => changes.push({ p: sn.p, v: false }));
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
  changes.push(...yearsOff(model));
  return changes;
};

// Main SEASONS group chip: OFF clears every season+decade under it; ON shows it + first season.
const clickMain = (model, M) => {
  if (M.v) {
    const changes = [{ p: M.p, v: false }];
    byRole(model, 'season').forEach((sn) => changes.push({ p: sn.p, v: false }));
    byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
    changes.push(...yearsOff(model));
    return changes;
  }
  const changes = [{ p: M.p, v: true }];
  const first = firstSeason(model);
  if (first) {
    const parentKey = key(first.p.slice(0, -1));
    changes.push({ p: first.p, v: true });
    byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: key(dn.p) === parentKey }));
    byRole(model, 'season').forEach((sn) => { if (sn !== first) changes.push({ p: sn.p, v: false }); });
  }
  model.singles.forEach((si) => changes.push({ p: si.p, v: false }));
  changes.push(...yearsOff(model), ...seqOff(model), ...seqGroupOff(model));
  return changes;
};

const clickDecade = (model, D) => {
  if (D.v) return [{ p: D.p, v: false }];
  const changes = [];
  if (main(model)) changes.push({ p: main(model).p, v: true });
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: dn === D }));
  changes.push(...yearsOff(model), ...seqOff(model), ...seqGroupOff(model));
  return changes;
};


// Year chips (Season YYYY leaves under the years group) — mutually exclusive with numbered
// seasons, decades, singles, and sequels. Mirrors clickSeason with the years group as the parent.
const clickYear = (model, Y) => {
  if (Y.v) return [{ p: Y.p, v: false }];
  const parentKey = key(Y.p.slice(0, -1));
  const changes = [{ p: Y.p, v: true }];
  if (main(model)) changes.push({ p: main(model).p, v: true });
  byRole(model, 'years').forEach((yg) => changes.push({ p: yg.p, v: key(yg.p) === parentKey }));
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
  byRole(model, 'season').forEach((sn) => changes.push({ p: sn.p, v: false }));
  byRole(model, 'year').forEach((yn) => { if (yn !== Y) changes.push({ p: yn.p, v: false }); });
  model.singles.forEach((si) => changes.push({ p: si.p, v: false }));
  changes.push(...seqOff(model), ...seqGroupOff(model));
  return changes;
};

// The years group bar — mirrors clickDecade: open this group, close the decades (and other years
// groups); the year leaves inside keep their state.
const clickYearsGroup = (model, YG) => {
  if (YG.v) return [{ p: YG.p, v: false }];
  const changes = [];
  if (main(model)) changes.push({ p: main(model).p, v: true });
  byRole(model, 'years').forEach((yg) => changes.push({ p: yg.p, v: yg === YG }));
  byRole(model, 'decade').forEach((dn) => changes.push({ p: dn.p, v: false }));
  changes.push(...seqOff(model), ...seqGroupOff(model));
  return changes;
};

module.exports = {
  key, seqs, allNodes, byRole, main, seqOff, seqGroupOff, yearsOff, firstSeason,
  clickSeason, clickSingle, clickSequel, clickMain, clickDecade, clickYear, clickYearsGroup,
};
