import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { assembleCatalog } from './replay.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.resolve(process.argv[2] ?? path.join(root, 'runtime/generated/replay'));
const read = (name) => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));
const queries = read('runtime/queries.json');
const names = ['kubernetes', 'proxmox', 'sparks', 'athena', 'network', 'power', 'devices'];
const groups = {}, missingDomains = [];
for (const name of names) {
  const file = `shared/src/domains/${name}/queries.json`;
  if (fs.existsSync(path.join(root, file))) groups[name] = read(file);
  else missingDomains.push(name);
}
const catalog = assembleCatalog({ base: read('runtime/catalog.json'), commonWindow: queries.window, bounded: read('fixtures/bounded.json'), inference: read('fixtures/inference.json'), domains: Object.values(groups).flat() });
const encoded = JSON.stringify(catalog);
const manifest = {
  schema: 1, catalogSha256: createHash('sha256').update(encoded).digest('hex'),
  windows: { daily: queries.window, inference: { from: Date.parse('2026-10-31T23:00:00Z'), to: Date.parse('2026-11-01T00:05:00Z') } },
  domains: Object.keys(groups), missingDomains,
  queries: catalog.reduce((n, e) => n + e.aliases.length, 0), canonicalEntries: catalog.length,
  missingCaptures: catalog.filter((e) => e.captureStatus === 'missing').flatMap((e) => e.aliases),
  capturedErrors: catalog.filter((e) => e.captureStatus === 'captured-error').map((e) => ({ refs: e.aliases, error: e.captureError })),
  sources: ['runtime/catalog.json', 'fixtures/bounded.json', 'fixtures/inference.json'].map((file) => ({ file, sha256: createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex') })),
};
fs.mkdirSync(out, { recursive: true });
fs.writeFileSync(path.join(out, 'catalog.json'), encoded);
fs.writeFileSync(path.join(out, 'queries.json'), JSON.stringify({ ...queries, domains: groups }, null, 2));
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
for (const file of ['server.mjs', 'replay.mjs']) fs.copyFileSync(path.join(root, 'runtime', file), path.join(out, file));
console.log(JSON.stringify(manifest, null, 2));
