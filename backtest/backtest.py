#!/usr/bin/env python3
"""Backtest and fit the ROVE snow model against SNOTEL observations.

Data comes from fetch_data.py (backtest/cache/ or $ROVE_CACHE).

Compared at each SNOTEL station's coordinates and elevation:
  Open-Meteo default   gfs_seamless snowfall, i.e. what an app sees if it uses Open-Meteo's US default as-is
  ROVE v2              model/model.json recipe, refitted out-of-sample (fit on one season, test on the other)

Truth
  liquid precip = SNOTEL PREC day-over-day increase
  new snow      = SNOTEL SNWD day-over-day increase (drops count as 0) x board factor,
                  where board factor = sum(COOP snowboard new snow) / sum(paired SNOTEL depth increase).
                  Depth increase undercounts what a snowboard measures because new snow settles within the day.

  python3 backtest/backtest.py              headline table (cross-validated) + bootstrap CIs + per-region
  python3 backtest/backtest.py --ablation   try each optional step on top of / removed from the final recipe
  python3 backtest/backtest.py --export     fit on all data and write model/model.json
"""
import itertools, json, os, sys, zlib
import datetime as dt
from collections import defaultdict
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get('ROVE_CACHE', os.path.join(HERE, 'cache'))
SEASONS = {'24-25': ('2024-12-01', '2025-03-31'), '25-26': ('2025-12-01', '2026-03-31')}
LEADS = [1, 3, 5, 7]
PM = ['ecmwf', 'gfs', 'icon', 'gem', 'nbm']
MODEL_ID = {'gfs': 'gfs_seamless', 'icon': 'icon_seamless', 'gem': 'gem_seamless', 'nbm': 'ncep_nbm_conus'}
BW = {'ecmwf': 0.4, 'gfs': 0.1, 'icon': 0.15, 'gem': 0.1, 'nbm': 0.15}   # starting model weights
BIG = 6.0   # "big snow day" threshold, inches
FINAL = ('trim_lead', 'slr', 'scale_mse')
nan = float('nan')


def nd(d, k):
  return (dt.date.fromisoformat(d) + dt.timedelta(days=k)).isoformat()


def key(sid):
  return sid.replace(':', '_')


def coop_pairs():
  """COOP snowboard stations paired with SNOTEL: {snotel_id: {date: board new snow}} and the pooled board factor.
  COOP reads in the morning, so reading on day D covers D-1 morning to D morning and is aligned to SNOTEL day D-1.
  Only stations with >=150 winter days and a plausible board/precip ratio (6-20) are used (drops windy plains sites)."""
  out, num, den = {}, 0.0, 0.0
  for f in os.listdir(CACHE):
    if not f.startswith('coop_'): continue
    d = json.load(open(os.path.join(CACHE, f)))
    if not d: continue
    sid = d['pair']['snotel']
    obs = json.load(open(os.path.join(CACHE, f'obs_{key(sid)}.json')))[0]['data']
    E = {e['stationElement']['elementCode']: {v['date']: v['value'] for v in e['values'] if v.get('value') is not None} for e in obs}
    sn, pr = E.get('SNWD', {}), E.get('PREC', {})
    C = {}
    for r in d['data']:
      try: C[nd(r[0], -1)] = 0.0 if r[1] == 'T' else float(r[1])
      except ValueError: pass
    a = b = c = n = 0.0
    for day, v in C.items():
      if day[5:7] not in ('12', '01', '02', '03'): continue
      q = nd(day, -1)
      if day in sn and q in sn and day in pr and q in pr:
        a += v; b += max(0, sn[day] - sn[q]); c += max(0, pr[day] - pr[q]); n += 1
    if n >= 150 and c and 6 <= a / c <= 20:
      out[sid] = dict(C=C, name=d['pair']['name'])
      num += a; den += b
  return out, (num / den if den else 1.0)


class Data:
  """Per lead: P[m] (N,24) hourly liquid precip, S[m] (N,24) model snowfall, T (N,24) temperature (ECMWF/GFS mean). NaN = missing."""

  def __init__(self, coop, board):
    sts = json.load(open(os.path.join(HERE, 'stations.json')))
    meta = []
    P = {L: defaultdict(list) for L in LEADS}; S = {L: defaultdict(list) for L in LEADS}; T = {L: [] for L in LEADS}
    for st in sts:
      po = os.path.join(CACHE, f"obs_{key(st['id'])}.json")
      if not os.path.exists(po): continue
      raw = defaultdict(dict)
      for el in json.load(open(po))[0]['data']:
        for v in el['values']:
          if v.get('value') is not None: raw[v['date']][el['stationElement']['elementCode']] = v['value']
      for sname in SEASONS:
        F = {}
        for k in ('ecmwf', 'multi', 'gfst'):
          p = os.path.join(CACHE, f"fc_{key(st['id'])}_{sname}_{k}.json")
          if os.path.exists(p): F[k] = json.load(open(p))['hourly']
        if len(F) < 3: continue
        byday = defaultdict(list)
        for i, t in enumerate(F['multi']['time']): byday[t[:10]].append(i)
        for d, idx in byday.items():
          if len(idx) != 24: continue
          a, b = raw.get(nd(d, -1), {}), raw.get(d, {})
          prec = None if a.get('PREC') is None or b.get('PREC') is None else b['PREC'] - a['PREC']
          if prec is not None and (prec < -0.5 or prec > 10): prec = None
          sn = None if a.get('SNWD') is None or b.get('SNWD') is None else b['SNWD'] - a['SNWD']
          if sn is not None and abs(sn) > 40: sn = None
          meta.append(dict(st=st['id'], area=st['area'], season=sname, d=d,
                           oP=nan if prec is None else max(0.0, prec), oS=nan if sn is None else max(0.0, sn)))
          arr = lambda v: [nan if x is None else x for x in (v[i] for i in idx)] if v else [nan] * 24
          for L in LEADS:
            P[L]['ecmwf'].append(arr(F['ecmwf'].get(f'precipitation_previous_day{L}')))
            te = arr(F['ecmwf'].get(f'temperature_2m_previous_day{L}')); tg = arr(F['gfst'].get(f'temperature_2m_previous_day{L}'))
            T[L].append(tg if np.isnan(te).all() else te if np.isnan(tg).all() else [(x + y) / 2 for x, y in zip(te, tg)])
            for m, mid in MODEL_ID.items():
              P[L][m].append(arr(F['multi'].get(f'precipitation_previous_day{L}_{mid}')))
              S[L][m].append(arr(F['multi'].get(f'snowfall_previous_day{L}_{mid}')))
    self.meta, self.N = meta, len(meta)
    self.oP = np.array([m['oP'] for m in meta])
    self.oS = np.array([m['oS'] for m in meta]) * board                       # snowboard-equivalent new snow
    self.oC = np.array([coop[m['st']]['C'].get(m['d'], nan) if m['st'] in coop else nan for m in meta])   # COOP board readings
    self.P = {L: {m: np.array(v) for m, v in P[L].items()} for L in LEADS}
    self.S = {L: {m: np.array(v) for m, v in S[L].items()} for L in LEADS}
    # Archive artefact: in Previous Runs, GFS precip beyond 120 h is 3-hourly totals interpolated as hourly values,
    # so lead 5/7 GFS totals come out at 0.39/0.28 of lead 1 (other models 0.82-1.09). Treat as missing.
    for L in (5, 7):
      self.P[L]['gfs'][:] = nan; self.S[L]['gfs'][:] = nan
    self.T = {L: np.array(T[L]) for L in LEADS}
    self.area = np.array([m['area'] for m in meta]); self.season = np.array([m['season'] for m in meta])
    self.st = np.array([m['st'] for m in meta])
    self.grp = np.array([f"{m['st']}|{m['season']}" for m in meta])


# ---------- baseline ----------
def omdef(D, L):
  return D.P[L]['gfs'].sum(1), D.S[L]['gfs'].sum(1)


# ---------- ROVE v2 ----------
def snow_frac(T, lo=33.0, hi=37.0):
  return np.clip((hi - T) / (hi - lo), 0, 1)


def slr(T, P):
  base, k, cap = P
  return np.minimum(cap, base + k * np.maximum(0, 32 - T))


def liquid(D, L, W):
  num = np.zeros((D.N, 24)); den = np.zeros((D.N, 1))
  for m, w in W.items():
    if not w: continue
    x = D.P[L][m]; ok = ~np.isnan(x).any(1, keepdims=True)
    num += np.where(ok, w * np.nan_to_num(x), 0); den += np.where(ok, w, 0)
  with np.errstate(invalid='ignore', divide='ignore'):
    return num / den


def v2_liq(D, L, M):
  h = liquid(D, L, M['W'][L])
  c = M.get('trim')
  c = c.get(L, 0) if isinstance(c, dict) else (c or 0)
  if c:   # drizzle trim: subtract c inches from the daily total, proportionally across hours
    tot = np.nansum(h, 1, keepdims=True)
    with np.errstate(invalid='ignore', divide='ignore'):
      h = h * np.where(tot > 0, np.maximum(0, tot - c) / tot, 0)
  if M.get('bias'):
    g = M['bias'].get(('*', L), 1.0)
    h = h * np.array([M['bias'].get((a, L), g) for a in D.area])[:, None]
  return h, D.T[L]


def v2_snow(D, L, M, h, T):
  R = slr(T, M['slr'])
  if M.get('scale'): R = R * M['scale'].get(L, 1.0)
  return (h * snow_frac(T, *M.get('frac', (33.0, 37.0))) * R).sum(1)


def v2(D, L, M):
  h, T = v2_liq(D, L, M)
  return h.sum(1), v2_snow(D, L, M, h, T)


# ---------- metrics ----------
def metrics(f, o, big=BIG, wet=1.0):
  ok = ~np.isnan(f) & ~np.isnan(o)
  f, o = f[ok], o[ok]
  if not len(f): return {}
  w = (f >= wet) | (o >= wet)
  hit = ((f >= big) & (o >= big)).sum(); miss = ((f < big) & (o >= big)).sum(); fa = ((f >= big) & (o < big)).sum()
  return dict(n=int(len(f)), mae=float(np.abs(f - o).mean()), wmae=float(np.abs(f - o)[w].mean()),
              mse=float(((f - o) ** 2).mean()), bias=float(f[w].sum() / o[w].sum()) if o[w].sum() else None,
              pod=float(hit / (hit + miss)) if hit + miss else None, far=float(fa / (hit + fa)) if hit + fa else None,
              csi=float(hit / (hit + miss + fa)) if hit + miss + fa else None, nbig=int(hit + miss))


# ---------- fitting (training rows only) ----------
def fit_weights(D, idx, L, step=0.1):
  ms = [m for m in PM if not np.isnan(D.P[L][m][idx]).all()]
  X = np.stack([D.P[L][m][idx].sum(1) for m in ms], 1); y = D.oP[idx]
  ok = ~np.isnan(X).any(1) & ~np.isnan(y); X, y = X[ok], y[ok]
  n = round(1 / step)
  combos = [list(c) + [n - sum(c)] for c in itertools.product(range(n + 1), repeat=len(ms) - 1) if sum(c) <= n]
  Wm = np.array(combos, float).T / n
  w = Wm[:, np.abs(X @ Wm - y[:, None]).mean(0).argmin()]
  return {m: float(x) for m, x in zip(ms, w)}


def fit_bias(D, idx, M, shrink=8.0):
  out = {}
  for L in LEADS:
    f = v2(D, L, dict(M, bias={}, slr=(10, 0, 10)))[0][idx]; o = D.oP[idx]; a = D.area[idx]
    ok = ~np.isnan(f) & ~np.isnan(o); g = o[ok].sum() / f[ok].sum()
    out[('*', L)] = float(g)
    for k in set(a[ok]):
      s = ok & (a == k)
      out[(k, L)] = float((o[s].sum() + shrink * g) / (f[s].sum() + shrink))
  return out


def fit_trim(D, idx, M):
  out = {}
  for L in LEADS:
    best = None
    for c in (0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
      p = v2(D, L, dict(M, trim={L: c}, bias={}))[0]; ok = idx & ~np.isnan(p) & ~np.isnan(D.oP)
      e = np.abs(p[ok] * (D.oP[ok].sum() / p[ok].sum()) - D.oP[ok]).mean()   # remove total bias first so trim isn't just shrinking totals
      if best is None or e < best[0]: best = (e, c)
    out[L] = best[1]
  return out


SLR_GRID = [(b, k, c) for b in (6, 7, 8, 9, 10, 11, 12, 13) for k in (0, 0.15, 0.3, 0.5, 0.7, 1.0) for c in (12, 15, 18, 22, 28)]


def fit_slr(D, idx, M, leads=(1, 3)):
  pre = {L: v2_liq(D, L, M) for L in leads}
  best = None
  for P in SLR_GRID:
    M2 = dict(M, slr=P)
    e = sum(metrics(v2_snow(D, L, M2, *pre[L])[idx], D.oS[idx])['wmae'] for L in leads)
    if best is None or e < best[0]: best = (e, P)
  return best[1]


def fit_scale(D, idx, M):
  out = {}
  for L in LEADS:
    s0 = v2(D, L, dict(M, scale=None))[1]; ok = idx & ~np.isnan(s0) & ~np.isnan(D.oS)
    ks = np.arange(0.4, 1.31, 0.05)
    out[L] = float(round(ks[np.argmin([((s0[ok] * k - D.oS[ok]) ** 2).mean() for k in ks])], 2))
  return out


def fit(D, idx, steps):
  M = dict(W={L: dict(BW) for L in LEADS}, bias={}, slr=(10, 0, 10))
  if 'w' in steps: M['W'] = {L: fit_weights(D, idx, L) for L in LEADS}
  if 'trim_lead' in steps: M['trim'] = fit_trim(D, idx, M)
  if 'bias' in steps: M['bias'] = fit_bias(D, idx, M)
  if 'slr' in steps: M['slr'] = fit_slr(D, idx, M)
  if 'scale_mse' in steps: M['scale'] = fit_scale(D, idx, M)
  return M


# ---------- evaluation ----------
def folds(D):
  have = [s for s in SEASONS if (D.season == s).any()]
  if len(have) == 2: return {s: D.season == s for s in have}
  h = np.array([zlib.crc32(s.encode()) % 2 for s in D.st])
  return {f'half{k}': h == k for k in (0, 1)}


def cv(D, steps):
  """Each row is predicted with parameters fitted on the other fold only."""
  outP = {L: np.full(D.N, nan) for L in LEADS}; outS = {L: np.full(D.N, nan) for L in LEADS}
  for _, test in folds(D).items():
    M = fit(D, ~test, steps)
    for L in LEADS:
      p, s = v2(D, L, M); outP[L][test] = p[test]; outS[L][test] = s[test]
  return {L: (outP[L], outS[L]) for L in LEADS}


def roll(D, x, k):
  """Non-overlapping k-day totals within a station-season (NaN if any day missing). Reduces day-boundary timing noise."""
  out = np.full(D.N, nan)
  starts = [i for i in range(D.N) if i == 0 or D.grp[i] != D.grp[i - 1]]
  for a, b in zip(starts, starts[1:] + [D.N]):
    for i in range(a, b - k + 1, k):
      out[i] = x[i:i + k].sum()
  return out


def boot_diff(D, a, b, truth, mask, n=1000, seed=7):
  """Station-resampled paired bootstrap of MAE(a) - MAE(b): point estimate and 90% interval. Negative = a is better."""
  ok = mask & ~np.isnan(a) & ~np.isnan(b) & ~np.isnan(truth)
  sts = np.unique(D.st[ok]); ea = np.abs(a - truth); eb = np.abs(b - truth)
  per = {s: (ea[ok & (D.st == s)].sum(), eb[ok & (D.st == s)].sum(), (ok & (D.st == s)).sum()) for s in sts}
  rnd = np.random.default_rng(seed); vals = []
  for _ in range(n):
    pick = rnd.choice(sts, len(sts))
    vals.append((sum(per[s][0] for s in pick) - sum(per[s][1] for s in pick)) / sum(per[s][2] for s in pick))
  vals = np.sort(vals)
  return float((ea[ok].sum() - eb[ok].sum()) / ok.sum()), float(vals[int(0.05 * n)]), float(vals[int(0.95 * n) - 1])


def fmt(x, k=2):
  return '' if x is None else f'{x:.{k}f}'


def headline(D):
  R = {'Open-Meteo default': {L: omdef(D, L) for L in LEADS}, 'ROVE v2': cv(D, FINAL)}
  oS3, oC3 = roll(D, D.oS, 3), roll(D, D.oC, 3)
  print(f'\nstations {len(set(D.st))}, station-days {D.N}, seasons {sorted(set(D.season))}')
  print('\n| Source | Lead | Daily MAE | Snow-day MAE | MSE | Total ratio | Big POD | Big FAR | Big CSI | 3-day MAE | 3-day big CSI | Board 3-day snow MAE |')
  print('|---|---|---|---|---|---|---|---|---|---|---|---|')
  for name, X in R.items():
    for L in LEADS:
      s = X[L][1]
      if np.isnan(s).all(): continue
      m1, m3, mc = metrics(s, D.oS), metrics(roll(D, s, 3), oS3), metrics(roll(D, s, 3), oC3)
      print(f"| {name} | {L} | {fmt(m1['mae'])} | {fmt(m1['wmae'])} | {fmt(m1['mse'])} | {fmt(m1['bias'])} | {fmt(m1['pod'])} | "
            f"{fmt(m1['far'])} | {fmt(m1['csi'])} | {fmt(m3['mae'])} | {fmt(m3['csi'])} | {fmt(mc.get('wmae'))} |")
  print('\nROVE v2 minus Open-Meteo default, MAE (station bootstrap, 90% CI; negative = v2 better)')
  print('| Lead | Daily | 3-day total |\n|---|---|---|')
  for L in (1, 3):
    a, b = R['ROVE v2'][L][1], R['Open-Meteo default'][L][1]
    d1 = boot_diff(D, a, b, D.oS, np.ones(D.N, bool)); d3 = boot_diff(D, roll(D, a, 3), roll(D, b, 3), oS3, ~np.isnan(oS3))
    print(f'| {L} | {d1[0]:+.2f} ({d1[1]:+.2f} to {d1[2]:+.2f}) | {d3[0]:+.2f} ({d3[1]:+.2f} to {d3[2]:+.2f}) |')
  print('\nPer region, 3-day snow-day MAE at lead 1 (Open-Meteo default -> ROVE v2)')
  print('| Region | Stations | MAE |\n|---|---|---|')
  for a in sorted(set(D.area)):
    mk = D.area == a
    x = metrics(roll(D, R['Open-Meteo default'][1][1], 3)[mk], oS3[mk]); y = metrics(roll(D, R['ROVE v2'][1][1], 3)[mk], oS3[mk])
    print(f"| {a} | {len(set(D.st[mk]))} | {fmt(x.get('wmae'))} -> {fmt(y.get('wmae'))} |")


def ablation(D):
  EXP = [('final', FINAL), ('final - trim', ('slr', 'scale_mse')), ('final - shrink', ('trim_lead', 'slr')),
         ('final + model weights', ('w',) + FINAL), ('final + regional bias', ('trim_lead', 'bias', 'slr', 'scale_mse')),
         ('ratio only', ('slr',))]
  print('\n| Recipe | Lead | Daily MAE | Snow-day MAE | Big CSI | 3-day MAE |\n|---|---|---|---|---|---|')
  oS3 = roll(D, D.oS, 3)
  for name, steps in EXP:
    R = cv(D, steps)
    for L in (1, 3):
      s = R[L][1]; m, m3 = metrics(s, D.oS), metrics(roll(D, s, 3), oS3)
      print(f"| {name} | {L} | {fmt(m['mae'])} | {fmt(m['wmae'])} | {fmt(m['csi'])} | {fmt(m3['mae'])} |")


def export(D, board):
  M = fit(D, np.ones(D.N, bool), FINAL)
  out = dict(steps=list(FINAL), stations=int(len(set(D.st))), days=int(D.N), seasons=sorted(set(D.season)),
             W={str(L): {m: round(w, 3) for m, w in M['W'][L].items() if w and not (m == 'gfs' and L >= 5)} for L in LEADS},
             bias={}, oro=None, slr=list(M['slr']), frac=[33.0, 37.0],
             trim={str(L): v for L, v in M['trim'].items()}, scale={str(L): v for L, v in M['scale'].items()}, board=round(board, 3))
  p = os.path.join(HERE, '..', 'model', 'model.json')
  json.dump(out, open(p, 'w'), indent=1)
  print('wrote', os.path.normpath(p), file=sys.stderr)


def main():
  coop, board = coop_pairs()
  print(f'board factor {board:.3f} from {len(coop)} COOP pairs', file=sys.stderr)
  D = Data(coop, board)
  if not D.N: sys.exit(f'no data in {CACHE}; run backtest/fetch_data.py first')
  if '--export' in sys.argv: return export(D, board)
  if '--ablation' in sys.argv: return ablation(D)
  headline(D)


if __name__ == '__main__':
  main()
