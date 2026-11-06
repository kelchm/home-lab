import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHarnessServer } from '../scenes/tests/browser/harness/serve.mjs';

test('missing harness outputs return 404 and the same server recovers after a build', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'preview harness space '));
  const server = createHarnessServer(root);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { await new Promise((resolve) => server.close(resolve)); fs.rmSync(root, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${server.address().port}`;
  assert.equal((await fetch(base + '/main.js')).status, 404);
  assert.equal((await fetch(base + '/a/homelab-observability-app/explore')).status, 404);
  fs.writeFileSync(path.join(root, 'main.js'), 'console.log("ok")');
  fs.writeFileSync(path.join(root, 'index.html'), '<div>built</div>');
  assert.equal(await (await fetch(base + '/main.js')).text(), 'console.log("ok")');
  assert.equal(await (await fetch(base + '/deep/link')).text(), '<div>built</div>');
  assert.equal((await fetch(base + '/icons/a.svg')).status, 200);
});
