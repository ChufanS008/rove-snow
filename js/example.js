#!/usr/bin/env node
/*
 * 15-day snow forecast for any point.
 *   node js/example.js <lat> <lon> <mid-mountain elevation in metres>
 *   node js/example.js 39.6042 -105.9538 3200        # e.g. a Colorado resort mid-mountain
 */
'use strict';
const { forecastDay, blendNws } = require('./rove-snow');
const { openMeteo, nwsGrid, nwsDay } = require('./fetch');

async function main() {
  const [lat, lon, elevation] = process.argv.slice(2).map(Number);
  if ([lat, lon, elevation].some((x) => !Number.isFinite(x))) {
    console.error('usage: node js/example.js <lat> <lon> <elevation_m>');
    process.exit(1);
  }
  const om = await openMeteo({ lat, lon, elevation });
  const grid = await nwsGrid({ lat, lon }).catch(() => null);
  const nwsOk = grid && grid.elevation != null && Math.abs(grid.elevation - elevation) <= 300;
  const nowHour = +new Date(Date.now() + om.utcOffset * 1000).toISOString().slice(11, 13);

  console.log(`timezone ${om.timezone}${grid ? `, NWS grid ${Math.round(grid.elevation)} m${nwsOk ? '' : ' (too far from mid-mountain, not blended)'}` : ''}`);
  console.log('date        lead  snow_in  rove_in  nws_in  slr');
  om.days.forEach((d, lead) => {
    const fromHour = lead === 0 ? nowHour : 0;
    const f = forecastDay({ precip: d.precip, temp: d.temp, lead, fromHour });
    if (!f) return;
    const nw = lead <= 2 && nwsOk ? nwsDay(grid, d.date, om.utcOffset, fromHour) : null;
    const snow = blendNws(f.snow, nw);
    console.log([d.date, String(lead).padStart(4), snow.toFixed(1).padStart(8), f.snow.toFixed(1).padStart(8),
      (nw == null ? '-' : nw.toFixed(1)).padStart(7), f.slr ? f.slr.toFixed(0).padStart(4) + ':1' : '   -'].join('  '));
  });
}

main().catch((e) => { console.error(e.message); process.exit(1); });
