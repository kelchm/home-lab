import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, test } from 'node:test';
import { digest, register, validateLock } from '../tools/prepare.mjs';
const folders = [];
afterEach(() => { for (const dir of folders.splice(0)) fs.rmSync(dir, { recursive: true, force: true }); });
const registry = 'packages/grafana-data/src/themes/registry.ts';
const selector = 'public/app/core/components/ThemeSelector/getSelectableThemes.ts';
function fixture() {
  const source = fs.mkdtempSync(path.join(os.tmpdir(), 'grafana-theme-')); folders.push(source);
  const files = {
    [registry]: "import zen from './themeDefinitions/zen.json';\nconst extraThemes: { [key: string]: unknown } = {\n zen,\n};\n",
    [selector]: "export function getSelectableThemes() {\nconst allowedExtraThemes = [\n 'zen',\n];\nreturn allowedExtraThemes;\n}\n",
  };
  for (const [name, text] of Object.entries(files)) { fs.mkdirSync(path.dirname(path.join(source, name)), { recursive: true }); fs.writeFileSync(path.join(source, name), text); }
  fs.mkdirSync(path.join(source, 'packages/grafana-data/src/themes/themeDefinitions'), { recursive: true });
  fs.writeFileSync(path.join(source, 'package.json'), JSON.stringify({ version: '13.3.0' }));
  return { source, files, lock: { version: '13.3.0', commit: 'a'.repeat(40), archiveSha256: 'b'.repeat(64), epoch: 1, stock: { image: 'grafana/grafana:13.3.0-distroless@sha256:' + 'c'.repeat(64), commit: 'd'.repeat(40), branch: 'release-13.3.0#patched', buildstamp: 1 }, files: Object.fromEntries(Object.entries(files).map(([k, v]) => [k, digest(v)])) }, definition: JSON.parse(fs.readFileSync(new URL('../theme.json', import.meta.url))) };
}
test('registers an independent theme and preserves the existing theme entries', () => {
  const f = fixture(); register(f.source, f.lock, f.definition);
  const result = fs.readFileSync(path.join(f.source, registry), 'utf8');
  assert.match(result, /import homelab_aurora/); assert.match(result, /\n zen,/);
  assert.match(fs.readFileSync(path.join(f.source, selector), 'utf8'), /'homelab_aurora'/);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.source, 'packages/grafana-data/src/themes/themeDefinitions/homelab_aurora.json'))), f.definition);
});
test('a changed upstream file fails before any source file is written', () => {
  const f = fixture(); fs.appendFileSync(path.join(f.source, selector), '// upstream changed');
  assert.throws(() => register(f.source, f.lock, f.definition), /Upstream source changed/);
  assert.equal(fs.readFileSync(path.join(f.source, registry), 'utf8'), f.files[registry]);
});
test('an ambiguous source anchor fails even if someone updates its hash', () => {
  const f = fixture(); const changed = f.files[selector] + f.files[selector];
  fs.writeFileSync(path.join(f.source, selector), changed); f.lock.files[selector] = digest(changed);
  assert.throws(() => register(f.source, f.lock, f.definition), /Expected one source anchor/);
  assert.equal(fs.readFileSync(path.join(f.source, registry), 'utf8'), f.files[registry]);
});
test('an existing upstream theme ID cannot be overwritten', () => {
  const f = fixture(); const file = path.join(f.source, 'packages/grafana-data/src/themes/themeDefinitions/homelab_aurora.json');
  fs.writeFileSync(file, 'upstream'); assert.throws(() => register(f.source, f.lock, f.definition), /already exists/); assert.equal(fs.readFileSync(file, 'utf8'), 'upstream');
});
test('the registration cannot be applied twice', () => {
  const f = fixture(); register(f.source, f.lock, f.definition); assert.throws(() => register(f.source, f.lock, f.definition), /Upstream source changed/);
});
test('a different source release cannot be patched with this lock', () => {
  const f = fixture(); f.lock.version = '13.4.0'; assert.throws(() => register(f.source, f.lock, f.definition));
});
test('the theme ID cannot drift away from the image default', () => {
  const f = fixture(); f.definition.id = 'another_theme'; assert.throws(() => register(f.source, f.lock, f.definition));
});
test('a valid source lock names precisely the two reviewed patch targets', () => {
  const f = fixture(); validateLock(f.lock); f.lock.files['another.ts'] = 'c'.repeat(64); assert.throws(() => validateLock(f.lock));
});
for (const [field, value] of [['commit', 'main'], ['archiveSha256', 'unknown'], ['version', 'latest'], ['epoch', -1]]) {
  test(`rejects an unpinned or invalid ${field}`, () => { const f = fixture(); f.lock[field] = value; assert.throws(() => validateLock(f.lock)); });
}
