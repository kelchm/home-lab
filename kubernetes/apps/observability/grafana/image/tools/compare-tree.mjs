// A different public source tree must never silently replace an official security-patched build.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { digest } from './prepare.mjs';

export function inventory(root) {
  const files = {};
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else { assert.ok(entry.isFile(), `Unexpected non-file ${full}`); files[path.relative(root, full)] = digest(fs.readFileSync(full)); }
    }
  };
  walk(root); return files;
}

export function compare(stock, built) {
  const a = inventory(stock), b = inventory(built);
  assert.ok(Object.keys(a).length > 0, 'Stock comparison tree is empty');
  const different = [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((name) => a[name] !== b[name]).sort();
  assert.deepEqual(different, [], 'Pristine build differs from the official distribution');
  return { status: 'identical', files: Object.keys(a).length, treeSha256: digest(JSON.stringify(Object.entries(a).sort())) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const report = compare(process.argv[2], process.argv[3]);
  fs.writeFileSync(process.argv[4], JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
}
