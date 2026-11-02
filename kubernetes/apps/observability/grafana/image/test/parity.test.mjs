import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { compare } from '../tools/compare-tree.mjs';

function trees(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'grafana-parity-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const stock = path.join(dir, 'stock'), built = path.join(dir, 'built');
  for (const root of [stock, built]) { fs.mkdirSync(path.join(root, 'nested'), { recursive: true }); fs.writeFileSync(path.join(root, 'nested/file.js'), 'const securityFix = true;'); }
  return { stock, built };
}
test('the parity gate compares exact bytes, including nested assets', (t) => {
  const { stock, built } = trees(t); assert.equal(compare(stock, built).files, 1);
  fs.writeFileSync(path.join(built, 'nested/file.js'), 'const securityFix = false;');
  assert.throws(() => compare(stock, built), /differs from the official/);
});
for (const kind of ['extra', 'missing']) {
  test(`the parity gate rejects ${kind} assets`, (t) => {
    const { stock, built } = trees(t);
    if (kind === 'extra') fs.writeFileSync(path.join(built, 'extra.js'), 'unexpected');
    else fs.unlinkSync(path.join(built, 'nested/file.js'));
    assert.throws(() => compare(stock, built), /differs from the official/);
  });
}
test('two empty comparison trees cannot pass', (t) => {
  const { stock, built } = trees(t);
  for (const root of [stock, built]) fs.unlinkSync(path.join(root, 'nested/file.js'));
  assert.throws(() => compare(stock, built), /empty/);
});
