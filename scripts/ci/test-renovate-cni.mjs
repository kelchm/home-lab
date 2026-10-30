// Use the same pinned Renovate internals as the other dependency-update tests.
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
const scratch = mkdtempSync(join(tmpdir(), 'renovate-cni-'));
after(() => rmSync(scratch, { recursive: true, force: true }));
GlobalConfig.set({ localDir: scratch, cacheDir: join(scratch, 'cache'), platform: 'github' });
const { config: resolvedConfig } = await resolveConfigPresets(JSON5.parse(readFileSync('.renovaterc.json5', 'utf8')));
const config = mergeChildConfig(getConfig(), resolvedConfig);
Object.assign(config, { errors: [], warnings: [], repoIsOnboarded: true, semanticCommits: 'enabled' });

async function extract(content, packageFile) {
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
  await add('kustomize', config.kustomize);
  await add('kubernetes', config.kubernetes);
  for (const manager of config.customManagers) await add(manager.customType, manager);
  return files;
}

const deps = (files) => Object.values(files).flatMap((packages) => packages.flatMap((file) => file.deps));
const release = (value) => value.replace(/^v/, '').replace(/-thick$/, '');

function assertAligned(content, files) {
  const kustomization = YAML.parse(content);
  const remote = kustomization.resources.filter((resource) => resource.startsWith('https://'));
  const sources = deps(files).filter((dep) => dep.datasource === 'github-tags');
  const images = deps(files).filter((dep) => dep.datasource === 'docker');
  assert.equal(sources.length, remote.length, 'every remote resource needs a tracked release and commit');
  assert.ok(sources.length > 0);
  assert.equal(images.length, 1, 'the image must be extracted exactly once');
  assert.equal(deps(files).length, sources.length + images.length, 'no duplicate extraction');
  assert.ok(deps(files).every((dep) => !dep.skipReason));
  assert.match(images[0].currentDigest, /^sha256:[a-f0-9]{64}$/, 'image needs an immutable digest');
  assert.equal(new Set(sources.map((dep) => dep.currentDigest)).size, 1, 'one source commit per app');
  for (const source of sources) {
    assert.match(source.currentDigest, /^[a-f0-9]{40}$/);
    assert.equal(release(source.currentValue), release(images[0].currentValue), 'manifest and image releases must match');
    assert.ok(remote.some((url) => url.startsWith(`https://raw.githubusercontent.com/${source.depName}/${source.currentDigest}/`)));
  }
}

function propose(dep, updateType) {
  const parts = release(dep.currentValue).split('.').map(Number);
  if (updateType !== 'digest') {
    const index = { major: 0, minor: 1, patch: 2 }[updateType];
    parts[index]++;
    parts.fill(0, index + 1);
  }
  const prefix = dep.currentValue.startsWith('v') ? 'v' : '';
  const suffix = dep.currentValue.endsWith('-thick') ? '-thick' : '';
  const newValue = `${prefix}${parts.join('.')}${suffix}`;
  dep.updates = [{ updateType, newValue, newVersion: newValue, newMajor: parts[0], newMinor: parts[1],
    newDigest: dep.datasource === 'docker' ? `sha256:${'e'.repeat(64)}` : 'f'.repeat(40) }];
}

for (const app of ['multus', 'whereabouts', 'generic-device-plugin']) {
  const packageFile = `kubernetes/apps/kube-system/${app}/app/kustomization.yaml`;
  const source = readFileSync(packageFile, 'utf8');
  test(`${app}: current manifests and image are aligned, immutable and extracted once`, async () => {
    assertAligned(source, await extract(source, packageFile));
  });

  for (const updateType of ['patch', 'minor', 'major']) {
    test(`${app}: ${updateType} replaces all source commits, release labels and the image in one reviewed branch`, async () => {
      const files = await extract(source, packageFile);
      for (const dep of deps(files)) propose(dep, updateType);
      const { branches } = await branchifyUpgrades(config, files);
      assert.equal(branches.length, 1);
      const branch = branches[0];
      assert.equal(branch.upgrades.length, deps(files).length);
      assert.equal(branch.minimumGroupSize, branch.upgrades.length);
      let updated = source;
      for (const upgrade of branch.upgrades) {
        assert.equal(upgrade.automerge, false);
        assert.equal(upgrade.pinDigests, true);
        updated = await doAutoReplace(upgrade, updated, false);
      }
      assertAligned(updated, await extract(updated, packageFile));
      assert.deepEqual(YAML.parse(updated).patches, YAML.parse(source).patches, 'local patches must survive');
      assert.deepEqual(YAML.parse(updated).resources.filter((r) => !r.startsWith('https://')),
        YAML.parse(source).resources.filter((r) => !r.startsWith('https://')));
    });
  }

  for (const datasource of ['docker', 'github-tags']) {
    test(`${app}: ${datasource} alone cannot meet the release group minimum`, async () => {
      const files = await extract(source, packageFile);
      for (const dep of deps(files).filter((dep) => dep.datasource === datasource)) propose(dep, 'patch');
      const { branches } = await branchifyUpgrades(config, files);
      assert.equal(branches.length, 1);
      assert.ok(branches[0].upgrades.length < branches[0].minimumGroupSize);
    });

    test(`${app}: same-release ${datasource} digest refresh keeps the release aligned`, async () => {
      const files = await extract(source, packageFile);
      for (const dep of deps(files).filter((dep) => dep.datasource === datasource)) propose(dep, 'digest');
      const { branches } = await branchifyUpgrades(config, files);
      assert.equal(branches.length, 1);
      assert.ok((branches[0].minimumGroupSize ?? 1) <= branches[0].upgrades.length);
      let updated = source;
      for (const upgrade of branches[0].upgrades) {
        assert.equal(upgrade.automerge, false);
        updated = await doAutoReplace(upgrade, updated, false);
      }
      assertAligned(updated, await extract(updated, packageFile));
    });
  }

  test(`${app}: CI rejects manual image drift, a missing annotation, or one source commit drifting`, async () => {
    const drifted = source.replace(/(newTag: v?\d+\.\d+\.)\d+/, '$199');
    await assert.rejects(async () => assertAligned(drifted, await extract(drifted, packageFile)), /releases must match/);
    const untracked = source.replace(/  # renovate: cni-source=.*\n/, '');
    await assert.rejects(async () => assertAligned(untracked, await extract(untracked, packageFile)), /every remote resource/);
    if (app === 'whereabouts') {
      const splitCommit = source.replace(/(raw\.githubusercontent\.com\/[^/]+\/[^/]+\/)[a-f0-9]{40}/, `$1${'a'.repeat(40)}`);
      await assert.rejects(async () => assertAligned(splitCommit, await extract(splitCommit, packageFile)), /one source commit/);
    }
  });
}

test('the CNI source manager does not claim other app manifests', () => {
  const manager = config.customManagers.find((entry) => entry.description?.includes('CNI manifests'));
  assert.deepEqual(getMatchingFiles(manager, ['kubernetes/apps/longhorn-system/longhorn/app/kustomization.yaml']), []);
});
