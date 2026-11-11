// node test/test.js : JS model vs Python port on the shared fixture, plus basic invariants.
'use strict';
const assert = require('assert');
const { execFileSync } = require('child_process');
const path = require('path');
const { forecastDay, blendNws, snowLiquidRatio, snowFraction } = require('../js/rove-snow');
const days = require('./fixture');

const js = days.map((d) => forecastDay(d));
const py = JSON.parse(execFileSync('python3', ['-I', path.join(__dirname, 'py_run.py')], { input: JSON.stringify(days) }).toString());
js.forEach((a, i) => {
  assert.ok(Math.abs(a.snow - py[i].snow) < 1e-9, `day ${i}: js ${a.snow} py ${py[i].snow}`);
  assert.ok(a.snow >= 0);
});
assert.strictEqual(snowFraction(30), 1); assert.strictEqual(snowFraction(40), 0);
assert.strictEqual(snowLiquidRatio(32), 6); assert.strictEqual(snowLiquidRatio(-40), 15);
assert.strictEqual(blendNws(0.6, 0), 0); assert.strictEqual(blendNws(5, null), 5);
assert.ok(Math.abs(blendNws(5, 10) - 9) < 1e-12);
assert.ok(js.filter((x) => x.snow > 0.5).length > 5, 'fixture should contain snowy days');
console.log(`ok: ${js.length} days, JS == Python, invariants hold`);
