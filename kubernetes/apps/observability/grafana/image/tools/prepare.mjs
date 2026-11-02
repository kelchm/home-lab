import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
const REGISTRY = 'packages/grafana-data/src/themes/registry.ts';
const SELECTOR = 'public/app/core/components/ThemeSelector/getSelectableThemes.ts';

export function register(source, lock, definition) {
  assert.equal(definition.id, 'homelab_aurora');
  assert.equal(definition.name, 'Homelab Aurora');
  assert.equal(definition.colors.mode, 'dark');
  assert.equal(JSON.parse(fs.readFileSync(path.join(source, 'package.json'))).version, lock.version);
  // Verify every patch target before writing anything. A changed upstream contract requires review.
  for (const [file, hash] of Object.entries(lock.files)) {
    assert.equal(digest(fs.readFileSync(path.join(source, file))), hash, `Upstream source changed: ${file}`);
  }
  const registry = fs.readFileSync(path.join(source, REGISTRY), 'utf8');
  const selector = fs.readFileSync(path.join(source, SELECTOR), 'utf8');
  const replaceOnce = (text, before, after) => {
    assert.equal(text.split(before).length - 1, 1, `Expected one source anchor: ${before}`);
    return text.replace(before, after);
  };
  const themeFile = `packages/grafana-data/src/themes/themeDefinitions/${definition.id}.json`;
  assert.ok(!fs.existsSync(path.join(source, themeFile)), 'Theme ID already exists upstream');
  let nextRegistry = replaceOnce(registry, "import zen from './themeDefinitions/zen.json';", "import zen from './themeDefinitions/zen.json';\nimport homelab_aurora from './themeDefinitions/homelab_aurora.json';");
  nextRegistry = replaceOnce(nextRegistry, 'const extraThemes: { [key: string]: unknown } = {', 'const extraThemes: { [key: string]: unknown } = {\n  homelab_aurora,');
  const nextSelector = replaceOnce(selector, 'const allowedExtraThemes = [', "const allowedExtraThemes = [\n    'homelab_aurora',");
  fs.writeFileSync(path.join(source, REGISTRY), nextRegistry);
  fs.writeFileSync(path.join(source, SELECTOR), nextSelector);
  fs.writeFileSync(path.join(source, themeFile), JSON.stringify(definition, null, 2) + '\n');
  return { themeId: definition.id, themeSha256: digest(JSON.stringify(definition)), files: [REGISTRY, SELECTOR, themeFile] };
}

export function validateLock(lock) {
  assert.match(lock.version, /^\d+\.\d+\.\d+$/);
  assert.match(lock.commit, /^[a-f0-9]{40}$/);
  assert.match(lock.archiveSha256, /^[a-f0-9]{64}$/);
  assert.ok(Number.isSafeInteger(lock.epoch) && lock.epoch > 0);
  assert.deepEqual(Object.keys(lock.files).sort(), [REGISTRY, SELECTOR].sort());
  for (const hash of Object.values(lock.files)) assert.match(hash, /^[a-f0-9]{64}$/);
  assert.match(lock.stock.image, /^grafana\/grafana:\d+\.\d+\.\d+-distroless@sha256:[a-f0-9]{64}$/);
  assert.match(lock.stock.commit, /^[a-f0-9]{40}$/);
  assert.match(lock.stock.branch, /^[a-zA-Z0-9.#_+-]+$/);
  assert.ok(Number.isSafeInteger(lock.stock.buildstamp) && lock.stock.buildstamp > 0);
}

async function main() {
  const input = path.resolve(process.argv[2] ?? '.');
  const output = path.resolve(process.argv[3] ?? '/work/grafana');
  const lock = JSON.parse(fs.readFileSync(path.join(input, 'source-lock.json')));
  validateLock(lock);
  assert.equal(process.argv[4], lock.stock.image, 'Runtime base must match the reviewed source lock');
  const stockVersion = fs.readFileSync('/work/stock-version', 'utf8').trim();
  assert.equal(stockVersion, `${lock.version} (commit: ${lock.stock.commit}, branch: ${lock.stock.branch})`, 'Runtime base provenance changed');
  const url = `https://codeload.github.com/grafana/grafana/tar.gz/${lock.commit}`;
  const response = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  assert.ok(response.ok, `Source download returned ${response.status}`);
  const archive = Buffer.from(await response.arrayBuffer());
  assert.equal(digest(archive), lock.archiveSha256, 'Source archive checksum mismatch');
  const archivePath = '/work/grafana-source.tar.gz';
  fs.writeFileSync(archivePath, archive);
  fs.mkdirSync(output, { recursive: true });
  assert.equal(fs.readdirSync(output).length, 0, 'Source output directory must be empty');
  const tar = spawnSync('tar', ['-xzf', archivePath, '--strip-components=1', '-C', output], { stdio: 'inherit' });
  assert.equal(tar.status, 0, 'Source extraction failed');
  assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'package.json'))).version, lock.version);
  fs.mkdirSync('/work/build-info', { recursive: true });
  for (const field of ['version', 'commit', 'epoch']) fs.writeFileSync(`/work/build-info/${field}`, String(lock[field]) + '\n');
  for (const field of ['commit', 'branch', 'buildstamp']) fs.writeFileSync(`/work/build-info/stock-${field}`, String(lock.stock[field]) + '\n');
  fs.writeFileSync('/work/source-manifest.json', JSON.stringify({ ...lock, url }, null, 2) + '\n');
  fs.rmSync(archivePath);
  console.log(`Verified pristine source archive ${lock.commit}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
