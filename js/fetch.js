/*
 * Fetch the inputs for rove-snow.js from free public APIs.
 *   Open-Meteo forecast API (CC BY 4.0): hourly precip + temperature for five models at a chosen elevation.
 *   NWS api.weather.gov (public domain): gridded snowfall for US points.
 * Requires Node 18+ (global fetch).
 */
'use strict';

const MODELS = { ecmwf_ifs025: 'ecmwf', gfs_seamless: 'gfs', icon_seamless: 'icon', gem_seamless: 'gem', ncep_nbm_conus: 'nbm' };
const UA = { 'user-agent': 'rove-snow' };
const qs = (o) => Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');

/**
 * @param {{lat:number, lon:number, elevation:number}} p  elevation in metres; use the resort's mid-mountain
 *        (the model was fitted at SNOTEL elevations, which sit at ~46% between base and summit)
 * @returns {Promise<{timezone:string, utcOffset:number, days:{date:string, precip:object, temp:object}[]}>}
 */
async function openMeteo({ lat, lon, elevation, days = 16 }) {
  const url = 'https://api.open-meteo.com/v1/forecast?' + qs({
    latitude: lat, longitude: lon, elevation: Math.round(elevation), cell_selection: 'nearest', timezone: 'auto',
    precipitation_unit: 'inch', temperature_unit: 'fahrenheit', hourly: 'precipitation,temperature_2m',
    forecast_days: days, models: Object.keys(MODELS).join(','),
  });
  const r = await fetch(url, { headers: UA });
  if (!r.ok) throw new Error(`Open-Meteo ${r.status}: ${await r.text()}`);
  const d = await r.json(), h = d.hourly, out = [];
  for (let s = 0; s + 24 <= h.time.length; s += 24) {
    const precip = {}, temp = {};
    for (const [id, k] of Object.entries(MODELS)) {
      const p = h['precipitation_' + id], t = h['temperature_2m_' + id];
      if (p) precip[k] = p.slice(s, s + 24);
      if (t) temp[k] = t.slice(s, s + 24);
    }
    out.push({ date: h.time[s].slice(0, 10), precip, temp });
  }
  return { timezone: d.timezone, utcOffset: d.utc_offset_seconds || 0, days: out };
}

/** NWS gridded snowfall → { elevation (m), hourly: { 'YYYY-MM-DDTHH' (UTC): inches } }. US only; null elsewhere. */
async function nwsGrid({ lat, lon }) {
  const hdr = Object.assign({ accept: 'application/geo+json' }, UA);
  const p = await fetch(`https://api.weather.gov/points/${lat.toFixed(4)},${lon.toFixed(4)}`, { headers: hdr });
  if (!p.ok) return null;
  const url = (await p.json()).properties.forecastGridData;
  const g = await fetch(url, { headers: hdr });
  if (!g.ok) return null;
  const pr = (await g.json()).properties, sf = pr.snowfallAmount;
  if (!sf || !sf.values) return null;
  const hourly = {};
  for (const { validTime, value } of sf.values) {
    const [t0, dur] = validTime.split('/');
    const m = /P(?:(\d+)D)?(?:T(?:(\d+)H)?)?/.exec(dur);
    const hrs = ((+m[1] || 0) * 24 + (+m[2] || 0)) || 1, start = Date.parse(t0);
    for (let k = 0; k < hrs; k++) hourly[new Date(start + k * 3600e3).toISOString().slice(0, 13)] = (value || 0) / 25.4 / hrs;
  }
  return { elevation: pr.elevation && pr.elevation.value, hourly };
}

/** Sum NWS hourly snowfall over one local day (from `fromHour` to midnight). Null if <80% of hours are covered. */
function nwsDay(grid, date, utcOffset, fromHour = 0) {
  if (!grid) return null;
  let s = 0, n = 0;
  for (let h = fromHour; h < 24; h++) {
    const k = new Date(Date.parse(`${date}T${String(h).padStart(2, '0')}:00:00Z`) - utcOffset * 1000).toISOString().slice(0, 13);
    if (k in grid.hourly) { s += grid.hourly[k]; n++; }
  }
  return n >= (24 - fromHour) * 0.8 ? s : null;
}

module.exports = { openMeteo, nwsGrid, nwsDay, MODELS };
