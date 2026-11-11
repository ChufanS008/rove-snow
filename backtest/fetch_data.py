#!/usr/bin/env python3
"""Download everything backtest.py needs into backtest/cache/ (or $ROVE_CACHE).

Forecasts: Open-Meteo Previous Runs API (CC BY 4.0) at each SNOTEL station's coordinates and elevation,
           hourly, issued 1/3/5/7 days ahead, per model:
             ecmwf_ifs025              precipitation, temperature_2m (no snowfall in this archive)
             gfs/icon/gem/nbm          precipitation, snowfall
             gfs_seamless              temperature_2m
Truth:     USDA NRCS SNOTEL daily PREC (accumulated precip), SNWD (snow depth), TAVG (public domain).
Snowboard: NOAA COOP / CoCoRaHS daily new snow via ACIS (public domain), paired with SNOTEL stations
           within 5 km and 500 ft, used to convert depth increase to snowboard-equivalent new snow.

Open-Meteo's free tier is rate-limited (10k calls/day); this script sleeps between requests and waits on 429.
A full download is ~2,800 forecast files (~550 MB) and takes most of a day. Re-running skips files already present.

  python3 backtest/fetch_data.py               # both seasons
  python3 backtest/fetch_data.py 25-26         # one season
"""
import json, math, os, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get('ROVE_CACHE', os.path.join(HERE, 'cache'))
SEASONS = {'24-25': ('2024-12-01', '2025-03-31'), '25-26': ('2025-12-01', '2026-03-31')}
LEADS = [1, 3, 5, 7]
UA = {'User-Agent': 'rove-snow-backtest'}
REQS = [
  ('ecmwf', 'ecmwf_ifs025', ['precipitation', 'temperature_2m']),
  ('multi', 'gfs_seamless,icon_seamless,gem_seamless,ncep_nbm_conus', ['precipitation', 'snowfall']),
  ('gfst', 'gfs_seamless', ['temperature_2m']),
]


def get(url, body=None):
  for i in range(30):
    try:
      req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                   headers=dict(UA, **({'Content-Type': 'application/json'} if body else {})))
      with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
      msg = e.read()[:300].decode('utf8', 'ignore')
      if e.code == 429:
        wait = 3600 if 'Daily' in msg else 300 if 'Hourly' in msg else 65
        print('429, waiting', wait, 's', file=sys.stderr, flush=True); time.sleep(wait); continue
      if e.code == 400: raise RuntimeError(msg)
      time.sleep(10 * (i + 1))
    except Exception as e:
      print('error', e, file=sys.stderr, flush=True); time.sleep(10 * (i + 1))
  raise RuntimeError('giving up on ' + url)


def km(a, b, c, d):
  return math.hypot(a - c, (b - d) * math.cos(math.radians(a))) * 111


def key(sid):
  return sid.replace(':', '_')


def main():
  os.makedirs(CACHE, exist_ok=True)
  sts = json.load(open(os.path.join(HERE, 'stations.json')))
  seasons = [a for a in sys.argv[1:] if a in SEASONS] or list(SEASONS)

  for st in sts:   # SNOTEL observations, both seasons in one call
    p = os.path.join(CACHE, f"obs_{key(st['id'])}.json")
    if not os.path.exists(p):
      d = get('https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?'
              f"stationTriplets={st['id']}&elements=PREC,SNWD,TAVG&duration=DAILY&beginDate=2024-11-25&endDate=2026-04-05")
      json.dump(d, open(p, 'w'))
  print('SNOTEL done', file=sys.stderr, flush=True)

  for st in sts:   # COOP snowboard pairs
    p = os.path.join(CACHE, f"coop_{key(st['id'])}.json")
    if os.path.exists(p): continue
    dd = 0.06
    meta = get('https://data.rcc-acis.org/StnMeta', dict(bbox=[st['lon'] - dd, st['lat'] - dd, st['lon'] + dd, st['lat'] + dd],
               elems='snow', sdate='2024-12-01', edate='2026-03-31', meta='name,sids,ll,elev'))
    best = None
    for m in (meta or {}).get('meta', []):
      if not m.get('ll') or m.get('elev') is None: continue
      k, de = km(st['lat'], st['lon'], m['ll'][1], m['ll'][0]), abs(m['elev'] - st['ft'])
      if k <= 5 and de <= 500 and (best is None or k < best[0]): best = (k, m)
    out = None
    if best:
      k, m = best
      sid = m['sids'][0].split()[0]
      data = get('https://data.rcc-acis.org/StnData', dict(sid=sid, sdate='2024-11-25', edate='2026-04-05', elems='snow,pcpn,snwd'))
      out = dict(pair=dict(snotel=st['id'], coop=sid, name=m['name'], km=round(k, 1)), data=(data or {}).get('data', []))
    json.dump(out, open(p, 'w'))
    time.sleep(0.3)
  print('COOP done', file=sys.stderr, flush=True)

  n = 0
  for sname in seasons:
    beg, end = SEASONS[sname]
    for st in sts:
      for k, models, vars_ in REQS:
        p = os.path.join(CACHE, f"fc_{key(st['id'])}_{sname}_{k}.json")
        if os.path.exists(p): continue
        hv = ','.join(f'{v}_previous_day{L}' for v in vars_ for L in LEADS)
        d = get('https://previous-runs-api.open-meteo.com/v1/forecast?'
                f"latitude={st['lat']}&longitude={st['lon']}&elevation={round(st['ft'] * 0.3048)}&cell_selection=nearest"
                f"&hourly={hv}&models={models}&precipitation_unit=inch&temperature_unit=fahrenheit"
                f"&timezone=auto&start_date={beg}&end_date={end}")
        json.dump(d, open(p + '.tmp', 'w')); os.replace(p + '.tmp', p)
        n += 1
        if n % 20 == 0: print(sname, n, st['id'], file=sys.stderr, flush=True)
        time.sleep(4)
  print('forecasts done', file=sys.stderr)


if __name__ == '__main__':
  main()
