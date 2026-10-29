import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { after, test } from 'node:test';
import { pathToFileURL } from 'node:url';

process.env.LOG_LEVEL ??= 'warn';
const renovateRoot = resolve(process.argv[2] ?? '/usr/local/renovate');
const require = createRequire(join(renovateRoot, 'package.json'));
const JSON5 = require('json5');
const load = (file) => import(pathToFileURL(join(renovateRoot, 'dist', file)));
const { GlobalConfig } = await load('config/global.js');
const { getConfig } = await load('config/defaults.js');
const { resolveConfigPresets } = await load('config/presets/index.js');
const { mergeChildConfig } = await load('config/utils.js');
const { extractPackageFile } = await load('modules/manager/index.js');
const { getMatchingFiles } = await load('workers/repository/extract/file-match.js');
const { normalizeDepNames } = await load('workers/repository/extract/manager-files.js');
const { branchifyUpgrades } = await load('workers/repository/updates/branchify.js');
const { doAutoReplace } = await load('workers/repository/update/branch/auto-replace.js');
const { init } = await load('logger/index.js');
await init();
const scratch = mkdtempSync(join(tmpdir(), 'renovate-caddy-'));
after(() => rmSync(scratch, { recursive: true, force: true }));
GlobalConfig.set({ localDir: scratch, cacheDir: join(scratch, 'cache'), platform: 'github' });
const { config: resolvedConfig } = await resolveConfigPresets(JSON5.parse(readFileSync('.renovaterc.json5', 'utf8')));
const config = mergeChildConfig(getConfig(), resolvedConfig);
Object.assign(config, { errors: [], warnings: [], repoIsOnboarded: true, semanticCommits: 'enabled' });
const packageFile = 'proxmox/guests/hermes-1/Dockerfile.caddy';
const source = readFileSync(packageFile, 'utf8');
const cloudflare = 'github.com/caddy-dns/cloudflare';
const digest = `sha256:${'f'.repeat(64)}`; // Replacement fixture, never published.
const pairedDigests = [`sha256:${'a'.repeat(64)}`, `sha256:${'b'.repeat(64)}`];

async function extract(content = source, fileName = packageFile) {
  const files = {};
  async function add(manager, managerConfig) {
    if (!getMatchingFiles(managerConfig, [fileName]).includes(fileName)) return;
    const result = await extractPackageFile(manager, content, fileName, managerConfig);
    if (!result) return;
    for (const dep of result.deps) {
      normalizeDepNames(dep);
      dep.updates = [];
    }
    (files[manager] ??= []).push({ ...result, packageFile: fileName });
  }
  await add('dockerfile', config.dockerfile);
  await add('docker-compose', config['docker-compose']);
  await add('github-actions', config['github-actions']);
  for (const manager of config.customManagers) await add(manager.customType, manager);
  return files;
}

function deps(files) {
  return Object.values(files).flatMap((packages) => packages.flatMap((file) => file.deps));
}

test('real Dockerfile extracts exactly two pinned bases and the actual module input', async () => {
  const files = await extract();
  const bases = deps(files).filter((dep) => dep.packageName === 'caddy');
  assert.equal(bases.length, 2);
  assert.ok(bases.every((dep) => dep.currentDigest?.match(/^sha256:[a-f0-9]{64}$/) && !dep.skipReason));
  assert.equal(bases[0].currentValue.split('-')[0], bases[1].currentValue.split('-')[0]);
  const modules = deps(files).filter((dep) => dep.depName === cloudflare);
  assert.equal(modules.length, 1);
  assert.equal(modules[0].datasource, 'go');
  assert.match(modules[0].currentValue, /^v\d+\.\d+\.\d+$/);
  // The old literal xcaddy argument was not tracked by the native manager.
  const before = source.replace(/# renovate: datasource=go[^\n]+\nARG CLOUDFLARE_VERSION=[^\n]+\n/, '');
  assert.equal(deps(await extract(before)).filter((dep) => dep.depName === cloudflare).length, 0);
});

test('real image overlay: native Compose manager changes only the digest, anytime and with review/tests', async () => {
  const fileName = 'proxmox/guests/hermes-1/compose.caddy-image.yaml';
  const content = readFileSync(fileName, 'utf8');
  const files = await extract(content, fileName);
  const images = deps(files);
  assert.equal(images.length, 1);
  const image = images[0];
  assert.equal(image.depName, 'ghcr.io/kelchm/home-lab/caddy-cloudflare');
  assert.equal(image.currentValue, 'candidate');
  assert.match(image.currentDigest, /^sha256:[a-f0-9]{64}$/);
  assert.ok(!image.skipReason);
  image.updates = [{ updateType: 'digest', newValue: 'candidate', newVersion: 'candidate', newDigest: digest }];
  const { branches } = await branchifyUpgrades(config, files);
  assert.equal(branches.length, 1);
  const upgrade = branches[0].upgrades[0];
  assert.equal(upgrade.automerge, false);
  assert.equal(upgrade.ignoreTests, false);
  assert.deepEqual(upgrade.schedule, ['at any time']);
  assert.equal(config.packageRules.find((rule) => rule.matchFileNames?.includes(fileName)).allowedVersions, '/^candidate$/');
  assert.equal(await doAutoReplace(upgrade, content, false), content.replace(image.currentDigest, digest));
});

for (const updateType of ['digest', 'patch', 'minor', 'major']) {
  test(`real BuildKit driver-opts: ${updateType} updates both occurrences in one reviewed branch`, async () => {
    const fileName = '.github/workflows/hermes-caddy-image.yaml';
    const content = readFileSync(fileName, 'utf8');
    const files = await extract(content, fileName);
    const drivers = deps(files).filter((dep) => dep.depName === 'moby/buildkit');
    assert.equal(drivers.length, 2, 'each driver must be extracted exactly once across all managers');
    assert.ok(drivers.every((dep) => !dep.skipReason));
    const [major, minor, patch] = drivers[0].currentValue.replace(/^v/, '').split('.').map(Number);
    const newValue = { digest: `v${major}.${minor}.${patch}`, patch: `v${major}.${minor}.${patch + 1}`,
      minor: `v${major}.${minor + 1}.0`, major: `v${major + 1}.0.0` }[updateType];
    for (const driver of drivers) driver.updates = [{ updateType, newValue, newVersion: newValue,
      newDigest: digest, newMajor: major, newMinor: minor }];
    const { branches } = await branchifyUpgrades(config, files);
    assert.equal(branches.length, 1);
    assert.equal(branches[0].upgrades.length, 2);
    assert.equal(branches[0].minimumGroupSize, 2);
    let updated = content;
    for (const upgrade of branches[0].upgrades) {
      assert.equal(upgrade.automerge, false);
      assert.equal(upgrade.ignoreTests, false);
      updated = await doAutoReplace(upgrade, updated, false);
    }
    assert.equal(updated, content.replaceAll(`moby/buildkit:${drivers[0].currentValue}@${drivers[0].currentDigest}`,
      `moby/buildkit:${newValue}@${digest}`));
  });
}

for (const updateType of ['patch', 'minor', 'major']) {
  test(`${updateType}: core release updates replace builder and runtime in one group`, async () => {
    const files = await extract();
    const requestedValues = [];
    for (const [baseIndex, dep] of deps(files).filter((dep) => dep.packageName === 'caddy').entries()) {
      const [version, ...suffix] = dep.currentValue.split('-');
      const parts = version.split('.').map(Number);
      const index = { major: 0, minor: 1, patch: 2 }[updateType];
      parts[index]++;
      parts.fill(0, index + 1);
      const newValue = [parts.join('.'), ...suffix].join('-');
      requestedValues.push(newValue);
      dep.updates = [{ updateType, newValue, newVersion: newValue, newDigest: pairedDigests[baseIndex], newMajor: parts[0], newMinor: parts[1] }];
    }
    const { branches } = await branchifyUpgrades(config, files);
    assert.equal(branches.length, 1);
    assert.equal(branches[0].upgrades.length, 2);
    assert.equal(branches[0].minimumGroupSize, 2);
    let updated = source;
    for (const upgrade of branches[0].upgrades) {
      assert.notEqual(upgrade.automerge, true, 'source update policy must not enable automerge');
      updated = await doAutoReplace(upgrade, updated, false);
    }
    const bases = deps(await extract(updated)).filter((dep) => dep.packageName === 'caddy');
    assert.deepEqual(bases.map((dep) => dep.currentValue), requestedValues);
    assert.deepEqual(bases.map((dep) => dep.currentDigest), pairedDigests);
    assert.equal(updated.split('\n').filter((line) => !line.startsWith('FROM ')).join('\n'),
      source.split('\n').filter((line) => !line.startsWith('FROM ')).join('\n'));
  });
}

for (const baseIndex of [0, 1]) {
  test(`base ${baseIndex}: same-release digest refresh is independent and changes only that digest`, async () => {
    const files = await extract();
    const bases = deps(files).filter((dep) => dep.packageName === 'caddy');
    const base = bases[baseIndex];
    base.updates = [{ updateType: 'digest', newValue: base.currentValue, newVersion: base.currentValue, newDigest: digest }];
    const { branches } = await branchifyUpgrades(config, files);
    assert.equal(branches.length, 1);
    assert.equal(branches[0].upgrades.length, 1);
    assert.notEqual(branches[0].minimumGroupSize, 2, 'digest refresh must not wait for the other base');
    const updated = await doAutoReplace(branches[0].upgrades[0], source, false);
    assert.equal(updated, source.replace(base.currentDigest, digest));
  });
}

test('module update replaces only the annotated ARG and keeps source automerge policy', async () => {
  const files = await extract();
  const module = deps(files).find((dep) => dep.depName === cloudflare);
  const parts = module.currentValue.slice(1).split('.').map(Number);
  parts[2]++;
  const newValue = `v${parts.join('.')}`;
  module.updates = [{ updateType: 'patch', newValue, newVersion: newValue, newMajor: parts[0], newMinor: parts[1] }];
  const { branches } = await branchifyUpgrades(config, files);
  assert.equal(branches.length, 1);
  assert.equal(branches[0].upgrades.length, 1);
  assert.notEqual(branches[0].upgrades[0].automerge, true);
  const updated = await doAutoReplace(branches[0].upgrades[0], source, false);
  assert.equal(updated, source.replace(`ARG CLOUDFLARE_VERSION=${module.currentValue}`, `ARG CLOUDFLARE_VERSION=${newValue}`));
});
