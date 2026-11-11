# rove-snow

An open, backtested snowfall model for ski resorts. It is the forecast that runs in [ROVE](https://rove-ski.com) for 640+ North American resorts.

Most weather apps turn model precipitation into snow at a fixed 10:1 or 7:1 ratio and report a snow-depth gain. Snow measured on a resort's snowboard is roughly double that, so those apps tend to under-report storms. rove-snow fixes that with three steps, each fitted on two winters of SNOTEL observations and checked out-of-sample:

1. **Blend five models' hourly liquid precipitation** (ECMWF, GFS, ICON, GEM, NBM) with weights by lead time, then trim each model's background drizzle.
2. **Turn precipitation into snow hour by hour.** Surface temperature sets both the rain/snow split (all snow below 33°F, all rain above 37°F) and the snow-to-liquid ratio (6:1 at 32°F, rising 0.7 per °F colder, capped at 15:1).
3. **Shrink the forecast toward climatology the further out it is** (×0.95 at 1–3 days, down to ×0.75 at a week).

The output is snowboard-equivalent new snow in inches at the elevation you request. For US resorts, ROVE also blends the next three days with the National Weather Service gridded snowfall; that step is included too.

## Accuracy

117 SNOTEL stations next to 137 resorts, December–March of 2024-25 and 2025-26, 28,314 station-days. Every number below is out-of-sample: parameters are fitted on one winter and scored on the other.

| Source | Lead (days) | Snow-day error (in) | Share of observed snow forecast | Big-day hit score (CSI, ≥6 in) | 3-day total error (in) |
|---|---|---|---|---|---|
| Open-Meteo default | 1 | 3.00 | 51% | 0.21 | 2.10 |
| **rove-snow** | 1 | **2.45** | **91%** | **0.39** | **1.72** |
| Open-Meteo default | 3 | 3.36 | 38% | 0.11 | 2.38 |
| **rove-snow** | 3 | **2.66** | **86%** | **0.29** | **1.97** |
| rove-snow | 5 | 2.90 | 78% | 0.19 | 2.28 |
| rove-snow | 7 | 3.06 | 68% | 0.11 | 2.55 |

- *Snow-day error* is the mean absolute error on days when the forecast or the observation is at least 1 in.
- *Open-Meteo default* is the `gfs_seamless` snowfall that a US app gets from Open-Meteo out of the box. The archive has no usable GFS beyond 5 days, so those rows are missing.
- Against snowboard readings from 8 co-located NOAA COOP stations, the 3-day snow-day error at lead 1 drops from 4.50 in to 3.46 in.
- 3-day total error, rove-snow minus Open-Meteo: −0.34 in at lead 1 (90% CI −0.41 to −0.28, station bootstrap).

| Region | Stations | 3-day snow-day error, lead 1 (in) |
|---|---|---|
| Colorado | 23 | 3.82 → 2.55 |
| Utah-Nevada | 15 | 4.93 → 3.43 |
| Wyoming-Montana | 24 | 3.45 → 2.57 |
| Idaho | 12 | 3.76 → 2.89 |
| New Mexico | 6 | 3.07 → 2.35 |
| Pacific Northwest | 18 | 4.00 → 3.47 |
| Tahoe | 9 | 6.03 → 5.14 |
| Other US (incl. Alaska) | 9 | 3.52 → 3.23 |

**Weak spots:** wet maritime snow (Tahoe, Pacific Northwest, Alaska). The 1.43 depth-to-snowboard factor comes from inland stations, and the snow-to-liquid curve is a single national fit.

**Tried and dropped:** refitted model weights, regional precipitation bias, flow-regime multipliers (700 hPa wind sector and speed, atmospheric rivers), a 700 hPa temperature snow ratio, regional snow ratios, and per-station bias. None of them reduced out-of-sample error by more than 0.01 in. `backtest.py --ablation` reruns several of them.

## Use it

### JavaScript (Node 18+, no dependencies)

```bash
node js/example.js 39.6042 -105.9538 3200     # lat, lon, mid-mountain elevation in metres
```

```js
const { forecastDay, blendNws } = require('./js/rove-snow');
const { openMeteo } = require('./js/fetch');

const { days } = await openMeteo({ lat: 39.6042, lon: -105.9538, elevation: 3200 });
days.forEach((d, lead) => console.log(d.date, forecastDay({ ...d, lead }).snow.toFixed(1)));
```

`forecastDay` takes plain arrays (24 hourly values per model), so you can feed it from any source.

### Python

```python
from python.rove_snow import forecast_day

precip = {'ecmwf': [0.04] * 24, 'gfs': [0.03] * 24}   # hourly liquid precip, inches
temp = {'ecmwf': [24.0] * 24, 'gfs': [26.0] * 24}      # hourly 2 m temperature, °F
forecast_day(precip, temp, lead=1)['snow']             # ≈ 9.3 in
```

### Which elevation

Use mid-mountain, halfway between base and summit. The model was fitted at SNOTEL elevations, which sit at about 46% of the way from base to summit. Run at summit elevation, the same parameters over-forecast total snow by 18–23%.

## Reproduce the backtest

```bash
pip install numpy
python3 backtest/fetch_data.py      # ~550 MB from Open-Meteo, SNOTEL and ACIS; rate-limited, takes most of a day
python3 backtest/backtest.py        # accuracy tables above
python3 backtest/backtest.py --ablation
python3 backtest/backtest.py --export   # refit model/model.json on all data
```

Truth is the SNOTEL daily snow-depth gain × 1.429, a factor fitted from the paired COOP snowboard stations. Depth gain alone undercounts because new snow settles during the day.

`node test/test.js` checks that the JavaScript and Python ports agree.

## Layout

```
model/model.json       fitted parameters
js/rove-snow.js        model (dependency-free)
js/fetch.js            Open-Meteo and NWS fetchers
js/example.js          15-day forecast CLI
python/rove_snow.py    Python port
backtest/              data download, backtest, fitting
test/                  cross-language parity test
```

## Data and credits

- Weather model data: [Open-Meteo](https://open-meteo.com) (CC BY 4.0), including the Previous Runs archive used for the backtest.
- Observations: USDA NRCS [SNOTEL](https://www.nrcs.usda.gov/wps/portal/wcc/home/) and NOAA COOP / CoCoRaHS via [ACIS](https://www.rcc-acis.org); both are public domain.
- Gridded forecasts: US National Weather Service [api.weather.gov](https://www.weather.gov/documentation/services-web-api) (public domain).

## License

Code and parameters: [MIT](LICENSE). The ROVE name and logo are not covered by the license.
