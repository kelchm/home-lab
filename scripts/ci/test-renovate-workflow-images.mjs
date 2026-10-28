#!/usr/bin/env node
// Run with the Renovate installation from the workflow's pinned image. These
// internal APIs deliberately fail loudly if a Renovate upgrade changes them.
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { after, test } from 'node:test';
import { pathToFileURL } from 'node:url';

process.env.LOG_LEVEL ??= 'warn';
const renovateRoot = resolve(process.argv[2] ?? '/usr/local/renovate');
const require = createRequire(join(renovateRoot, 'package.json'));
const JSON5 = require('json5');
const YAML = require('yaml');
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

const scratch = mkdtempSync(join(tmpdir(), 'renovate-workflow-images-'));
after(() => rmSync(scratch, { recursive: true, force: true }));
GlobalConfig.set({ localDir: scratch, cacheDir: join(scratch, 'cache'), platform: 'github' });
const { config: resolvedConfig } = await resolveConfigPresets(
  JSON5.parse(readFileSync('.renovaterc.json5', 'utf8')),
);
const config = mergeChildConfig(getConfig(), resolvedConfig);
Object.assign(config, { errors: [], warnings: [], repoIsOnboarded: true, semanticCommits: 'enabled' });
const packageFile = '.github/workflows/repository-validation.yaml';
const workflow = readFileSync(packageFile, 'utf8');
const flate = 'ghcr.io/home-operations/flate';
const kubeconform = 'ghcr.io/yannh/kubeconform';
const frr = 'quay.io/frrouting/frr';
const digest = (character) => `sha256:${character.repeat(64)}`;
const fixture = String.raw`
name: Representative workflow
on: push
jobs:
  render:
    runs-on: ubuntu-latest
    steps:
      - uses: docker://${flate}:1.2.3@${digest('a')}
  diff:
    runs-on: ubuntu-latest
    steps:
      - run: >-
          docker run --rm
          --volume "x:/workspace:ro"
          ${flate}:1.2.3@${digest('a')}
          diff hr --path pull --path-orig base
      - run: |
          docker run --rm \
            --volume "x:/workspace:ro" \
            ${kubeconform}:v0.8.0-amd64@${digest('b')} \
            -strict /workspace/example.yaml
      - run: >-
          docker run --rm
          --entrypoint vtysh
          ${frr}:10.3.4@${digest('c')}
          -C -f /workspace/frr.conf
`;

async function extract(content) {
  const files = {};
  async function add(manager, managerConfig) {
    if (!getMatchingFiles(managerConfig, [packageFile]).includes(packageFile)) return;
    const result = await extractPackageFile(manager, content, packageFile, managerConfig);
    if (!result) return;
    for (const dep of result.deps) {
      normalizeDepNames(dep);
      dep.updates = [];
    }
    (files[manager] ??= []).push({ ...result, packageFile });
  }
  await add('github-actions', config['github-actions']);
  for (const manager of config.customManagers) {
    await add(manager.customType, manager);
  }
  return files;
}

function dependencies(files) {
  return Object.entries(files).flatMap(([manager, packages]) => packages.flatMap(
    (file) => file.deps.map((dep) => ({ manager, ...dep })),
  ));
}

function assertFlateAligned(files) {
  const refs = dependencies(files).filter((dep) => dep.depName === flate);
  assert.equal(refs.length, 2, 'both Flate jobs must be extracted exactly once');
  assert.deepEqual(new Set(refs.map((dep) => dep.manager)), new Set(['github-actions', 'regex']));
  assert.deepEqual(
    refs.map(({ currentValue, currentDigest }) => ({ currentValue, currentDigest })),
    Array(2).fill({ currentValue: refs[0].currentValue, currentDigest: refs[0].currentDigest }),
    'Flate render and diff must have the same tag and digest',
  );
}

for (const [name, content] of [['workflow', workflow], ['multiline fixture', fixture]]) {
  test(`${name}: every inline docker image is tracked; Flate starts aligned`, async () => {
    const files = await extract(content);
    assertFlateAligned(files);
    // Read the shell commands through YAML, independently of the custom regex.
    // Their pinned image tokens must equal the dependencies Renovate extracted.
    const commands = Object.values(YAML.parse(content).jobs).flatMap(
      (job) => job.steps.map((step) => step.run ?? '').filter((run) => run.includes('docker run')),
    );
    const images = commands.flatMap((run) => run.split(/\s+/).filter((token) => token.includes('@sha256:')));
    const extracted = dependencies(files).filter((dep) => dep.manager === 'regex' && dep.datasource === 'docker');
    assert.equal(images.length, 3);
    assert.deepEqual(
      extracted.map((dep) => `${dep.depName}:${dep.currentValue}@${dep.currentDigest}`).sort(),
      images.sort(),
    );
    assert.ok(extracted.every((dep) => !dep.skipReason));
  });

  for (const updateType of ['digest', 'patch', 'minor', 'major']) {
    test(`${name}: ${updateType} update groups and replaces both Flate jobs`, async () => {
      const files = await extract(content);
      const current = dependencies(files).find((dep) => dep.depName === flate);
      const parts = current.currentValue.split('.').map(Number);
      const index = { major: 0, minor: 1, patch: 2 }[updateType];
      if (index !== undefined) {
        parts[index] += 1;
        parts.fill(0, index + 1);
      }
      const newValue = parts.join('.');
      const newDigest = digest('f');
      for (const packages of Object.values(files)) {
        for (const file of packages) {
          for (const dep of file.deps.filter((dep) => dep.depName === flate)) {
            dep.updates = [{ updateType, newValue, newVersion: newValue, newDigest, newMajor: parts[0], newMinor: parts[1] }];
          }
        }
      }
      const { branches } = await branchifyUpgrades(config, files);
      assert.equal(branches.length, 1, 'one Flate update branch across managers');
      const branch = branches[0];
      assert.equal(branch.upgrades.length, 2);
      assert.equal(branch.minimumGroupSize, 2, 'withhold a branch missing either job');
      for (const upgrade of branch.upgrades) {
        assert.equal(upgrade.automerge, false, 'both jobs require review');
        assert.equal(upgrade.ignoreTests, false);
        assert.equal(upgrade.minimumReleaseAge, '3 days', 'manager eligibility must agree');
      }
      let updated = content;
      for (const upgrade of branch.upgrades) updated = await doAutoReplace(upgrade, updated, false);
      assert.equal(updated, content.replaceAll(
        `${flate}:${current.currentValue}@${current.currentDigest}`,
        `${flate}:${newValue}@${newDigest}`,
      ), 'only the two image references may change');
      assertFlateAligned(await extract(updated));
    });
  }
}

test('the alignment guard rejects a tag or digest changed in only one Flate job', async () => {
  for (const changed of [`${flate}:1.2.4@${digest('a')}`, `${flate}:1.2.3@${digest('d')}`]) {
    const drifted = fixture.replace(`${flate}:1.2.3@${digest('a')}`, changed);
    const files = await extract(drifted);
    assert.throws(() => assertFlateAligned(files), /same tag and digest/);
  }
});

for (const depName of [kubeconform, frr]) {
  test(`${depName}: replace tag and digest without changing the surrounding command`, async () => {
    const files = await extract(fixture);
    const file = files.regex.find((file) => file.deps.some((dep) => dep.depName === depName));
    const depIndex = file.deps.findIndex((dep) => dep.depName === depName);
    const dep = file.deps[depIndex];
    const newValue = dep.currentValue.replace(/\d+/, (number) => Number(number) + 1);
    const newDigest = digest('e');
    const updated = await doAutoReplace({ ...file, ...dep, manager: 'regex', depIndex, newValue, newDigest }, fixture, false);
    assert.equal(updated, fixture.replace(
      `${depName}:${dep.currentValue}@${dep.currentDigest}`,
      `${depName}:${newValue}@${newDigest}`,
    ));
  });
}
