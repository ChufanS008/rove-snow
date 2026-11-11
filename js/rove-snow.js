/*
 * ROVE snow model v2 — daily new-snow forecast for ski resorts.
 *
 * One day's snow (inches, snowboard measure) =
 *   Σ over 24 local hours of
 *     blended liquid precip (weighted across models by lead time, light drizzle trimmed)
 *     × snow fraction (from surface temperature)
 *     × snow-to-liquid ratio (from surface temperature)
 *   × lead-time shrink factor.
 *
 * Parameters live in ../model/model.json and are fitted by ../backtest/backtest.py
 * on 117 SNOTEL stations over two winters (24-25, 25-26), cross-validated season against season.
 *
 * Inputs are plain arrays so this file has no dependencies and runs in Node or a browser.
 */
'use strict';

const DEFAULT_MODEL = require('../model/model.json');

const sum = (a) => a.reduce((x, y) => x + y, 0);

/** Lead days 0–1 use the "1" parameter set, 2–3 use "3", 4–5 use "5", 6+ use "7". */
const leadKey = (lead) => (lead <= 1 ? 1 : lead <= 3 ? 3 : lead <= 5 ? 5 : 7);

/** Share of precipitation falling as snow at surface temperature T (°F): all snow below frac[0], all rain above frac[1]. */
function snowFraction(T, M = DEFAULT_MODEL) {
  const [lo, hi] = M.frac;
  return Math.min(1, Math.max(0, (hi - T) / (hi - lo)));
}

/** Snow-to-liquid ratio at T (°F): base at 32°F, +k per degree below 32°F, capped. slr = [base, k, cap]. */
function snowLiquidRatio(T, M = DEFAULT_MODEL) {
  const [base, k, cap] = M.slr;
  return Math.min(cap, base + k * Math.max(0, 32 - T));
}

/**
 * Forecast one local calendar day.
 *
 * @param {object} day
 * @param {Object<string, number[]>} day.precip  hourly liquid precip in inches, 24 values per model,
 *                                               keys: ecmwf, gfs, icon, gem, nbm (missing models are skipped and weights renormalised)
 * @param {Object<string, number[]>} day.temp    hourly 2 m temperature in °F, 24 values; ecmwf and/or gfs are averaged
 * @param {number} day.lead                      days ahead (0 = today)
 * @param {number} [day.fromHour=0]              first local hour to count (pass the current hour for "today")
 * @param {object} [M]                           model parameters (defaults to model/model.json)
 * @returns {null | {snow:number, liquid:number, slr:number|null, hourly:{snow:number[], liquid:number[], fraction:number[], slr:number[]}}}
 */
function forecastDay({ precip, temp, lead, fromHour = 0 }, M = DEFAULT_MODEL) {
  const L = leadKey(lead), W = M.W[L];
  let liq = [];
  const tmp = [];
  for (let i = 0; i < 24; i++) {
    let num = 0, den = 0;
    for (const m in W) {
      const x = precip[m] && precip[m][i];
      if (W[m] && x != null) { num += W[m] * x; den += W[m]; }
    }
    const ts = ['ecmwf', 'gfs'].map((m) => temp[m] && temp[m][i]).filter((x) => x != null);
    if (!den || !ts.length) return null;
    liq.push(num / den);
    tmp.push(sum(ts) / ts.length);
  }

  // Trim model drizzle: subtract `trim` inches from the daily total, removed proportionally from each hour.
  const total = sum(liq), trim = (M.trim && M.trim[L]) || 0;
  const keep = total > 0 ? Math.max(0, total - trim) / total : 0;
  liq = liq.map((x) => x * keep);

  const shrink = (M.scale && M.scale[L]) || 1;
  const hs = [], hf = [], hr = [];
  let snow = 0, wsum = 0, ssum = 0;
  liq.forEach((p, j) => {
    const f = snowFraction(tmp[j], M), r = snowLiquidRatio(tmp[j], M);
    hs.push(p * f * r * shrink); hf.push(f); hr.push(r);
    if (j < fromHour) return;
    snow += p * f * r; ssum += p * f * r; wsum += p * f;
  });
  return {
    snow: snow * shrink,
    liquid: sum(liq.slice(fromHour)),
    slr: wsum > 0.01 ? ssum / wsum : null,   // precip-weighted ratio for the day, useful as a snow-quality signal
    hourly: { snow: hs, liquid: liq, fraction: hf, slr: hr },
  };
}

/**
 * US resorts, lead 0–2: blend with the NWS gridded snowfall (forecaster-edited NDFD).
 * Weight 0.8 NWS / 0.2 ROVE is provisional (NWS has no public forecast archive to backtest);
 * it was chosen so no resort exceeded a 1.5× / >1 in disagreement with NWS in a 138-resort check.
 * Skip the blend when the NWS grid cell elevation is more than 300 m from the resort mid-mountain.
 */
const NWS_WEIGHT = 0.8;
function blendNws(roveSnow, nwsSnow, w = NWS_WEIGHT) {
  if (nwsSnow == null) return roveSnow;
  if (nwsSnow === 0 && roveSnow < 1) return 0;
  return w * nwsSnow + (1 - w) * roveSnow;
}

/** Converts a measured snow-depth increase (SNOTEL, automatic stations) to snowboard-equivalent new snow. */
const depthToBoard = (dDepth, M = DEFAULT_MODEL) => Math.max(0, dDepth) * (M.board || 1.43);

module.exports = { forecastDay, blendNws, snowFraction, snowLiquidRatio, depthToBoard, leadKey, NWS_WEIGHT, DEFAULT_MODEL };
