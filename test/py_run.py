import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'python'))
from rove_snow import forecast_day, load_model
M = load_model()
print(json.dumps([forecast_day(d['precip'], d['temp'], d['lead'], M, d.get('fromHour', 0)) for d in json.load(sys.stdin)]))
