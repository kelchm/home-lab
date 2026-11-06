import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

test('assembly discovers new query directories and reports unavailable expected domains from a path with spaces', (t) => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'preview assembly space '));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  for (const dir of ['runtime', 'shared/src/domains/new-system', 'fixtures']) fs.mkdirSync(path.join(temp, dir), { recursive: true });
  for (const name of ['assemble.mjs', 'replay.mjs', 'server.mjs']) fs.copyFileSync(new URL(name, import.meta.url), path.join(temp, 'runtime', name));
  const write = (file, value) => fs.writeFileSync(path.join(temp, file), JSON.stringify(value));
  write('runtime/queries.json', { common: [], network: [], window: { from: 1000, to: 2000 } });
  write('runtime/catalog.json', []);
  write('fixtures/bounded.json', { from: 1000, to: 2000 });
  write('fixtures/inference.json', []);
  write('shared/src/domains/new-system/queries.json', [{ refId: 'D_new', expr: 'new_metric', instant: true, range: false }]);
  const out = path.join(temp, 'generated output');
  execFileSync(process.execPath, [path.join(temp, 'runtime/assemble.mjs'), out]);
  const manifest = JSON.parse(fs.readFileSync(path.join(out, 'manifest.json')));
  assert.deepEqual(manifest.domains, ['new-system']);
  assert.equal(manifest.missingDomains.length, 7);
  assert.deepEqual(manifest.missingCaptures, ['D_new']);
  assert.equal(JSON.parse(fs.readFileSync(path.join(out, 'catalog.json')))[0].refId, 'D_new');
});
