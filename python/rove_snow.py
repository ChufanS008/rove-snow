"""ROVE snow model v2 (Python port of js/rove-snow.js).

One day's snow (inches, snowboard measure) =
  sum over 24 local hours of
    blended liquid precip (model weights by lead time, light drizzle trimmed)
    x snow fraction(T) x snow-to-liquid ratio(T)
  x lead-time shrink factor.

Works on single days (lists) or batches (numpy arrays shaped (N, 24)).
"""
import json
import os

import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'model', 'model.json')


def load_model(path=MODEL_PATH):
  with open(path) as f:
    return json.load(f)


def lead_key(lead):
  return 1 if lead <= 1 else 3 if lead <= 3 else 5 if lead <= 5 else 7


def snow_fraction(T, frac=(33.0, 37.0)):
  """Surface temperature (°F) -> share of precipitation falling as snow."""
  lo, hi = frac
  return np.clip((hi - np.asarray(T, float)) / (hi - lo), 0, 1)


def snow_liquid_ratio(T, slr=(6, 0.7, 15)):
  """Snow-to-liquid ratio: base at 32°F, +k per degree below 32°F, capped."""
  base, k, cap = slr
  return np.minimum(cap, base + k * np.maximum(0, 32 - np.asarray(T, float)))


def forecast_day(precip, temp, lead, M=None, from_hour=0):
  """precip: {model: 24 hourly inches}, temp: {'ecmwf'/'gfs': 24 hourly °F}. Returns dict or None."""
  M = M or load_model()
  L = str(lead_key(lead))
  W = M['W'][L]
  liq = np.zeros(24)
  den = 0.0
  for m, w in W.items():
    x = precip.get(m)
    if w and x is not None and None not in x:
      liq += w * np.asarray(x, float)
      den += w
  ts = [np.asarray(temp[m], float) for m in ('ecmwf', 'gfs') if temp.get(m) is not None and None not in temp[m]]
  if not den or not ts:
    return None
  liq /= den
  T = np.mean(ts, axis=0)
  tot = liq.sum()
  trim = M.get('trim', {}).get(L, 0)
  liq *= max(0.0, tot - trim) / tot if tot > 0 else 0.0
  f = snow_fraction(T, M['frac'])
  r = snow_liquid_ratio(T, M['slr'])
  shrink = (M.get('scale') or {}).get(L, 1.0)
  hs = liq * f * r
  sl = slice(from_hour, 24)
  wsum = (liq * f)[sl].sum()
  return dict(snow=float(hs[sl].sum() * shrink), liquid=float(liq[sl].sum()),
              slr=float(hs[sl].sum() / wsum) if wsum > 0.01 else None, hourly_snow=(hs * shrink).tolist())


def blend_nws(rove_snow, nws_snow, w=0.8):
  """US resorts, lead 0-2: provisional 0.8 NWS / 0.2 ROVE blend."""
  if nws_snow is None:
    return rove_snow
  if nws_snow == 0 and rove_snow < 1:
    return 0.0
  return w * nws_snow + (1 - w) * rove_snow
