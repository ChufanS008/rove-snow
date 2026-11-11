// Deterministic synthetic days (cold storm, rain/snow line, warm rain, missing NBM) used by both language tests.
'use strict';
function rng(seed) { return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32); }
const r = rng(42), days = [];
const cases = [[18, 0.08], [33, 0.05], [36, 0.06], [45, 0.04], [25, 0.0], [30, 0.12]];
cases.forEach(([t0, p0], ci) => {
  for (const lead of [0, 1, 2, 4, 6, 9]) {
    const precip = {}, temp = {};
    for (const m of ['ecmwf', 'gfs', 'icon', 'gem', 'nbm']) precip[m] = Array.from({ length: 24 }, () => +(p0 * r() * 2).toFixed(3));
    if (ci === 5) delete precip.nbm;
    for (const m of ['ecmwf', 'gfs']) temp[m] = Array.from({ length: 24 }, (_, h) => +(t0 + 4 * Math.sin(h / 4) + r() * 2).toFixed(1));
    days.push({ precip, temp, lead, fromHour: lead === 0 ? 10 : 0 });
  }
});
module.exports = days;
if (require.main === module) process.stdout.write(JSON.stringify(days));
