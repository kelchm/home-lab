// Use the same pinned Renovate internals as the other dependency-update tests.
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
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
const scratch = mkdtempSync(join(tmpdir(), 'renovate-oci-charts-'));
after(() => rmSync(scratch, { recursive: true, force: true }));
GlobalConfig.set({ localDir: scratch, cacheDir: join(scratch, 'cache'), platform: 'github' });
const { config: resolvedConfig } = await resolveConfigPresets(JSON5.parse(readFileSync('.renovaterc.json5', 'utf8')));
const config = mergeChildConfig(getConfig(), resolvedConfig);
Object.assign(config, { errors: [], warnings: [], repoIsOnboarded: true, semanticCommits: 'enabled' });

const chartLayer = 'application/vnd.cncf.helm.chart.content.v1.tar+gzip';
const newDigest = `sha256:${'e'.repeat(64)}`;

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
  await add('flux', config.flux);
  await add('kubernetes', config.kubernetes);
  for (const manager of config.customManagers) await add(manager.customType, manager);
  return files;
}

// The kubernetes manager also records each object's apiVersion; that is not a package reference.
const deps = (files) => Object.values(files).flatMap((packages) => packages.flatMap((file) => file.deps))
  .filter((dep) => dep.datasource !== 'kubernetes-api');
const isChartSource = (doc) => doc?.kind === 'OCIRepository' && doc.spec?.layerSelector?.mediaType === chartLayer;
const sources = readdirSync('kubernetes', { recursive: true })
  .filter((file) => /\.ya?ml$/.test(file) && !file.includes('.sops.'))
  .map((file) => join('kubernetes', file))
  .filter((file) => YAML.parseAllDocuments(readFileSync(file, 'utf8')).some((doc) => isChartSource(doc.toJS())));

function assertPinned(content, files) {
  const { spec } = YAML.parse(content);
  assert.equal(deps(files).length, 1, 'a chart source must be extracted exactly once');
  const [dep] = deps(files);
  assert.equal(files.flux?.length, 1, 'the flux manager must own chart sources');
  assert.equal(dep.depType, 'HelmChart');
  assert.equal(dep.datasource, 'docker');
  assert.equal(dep.skipReason, undefined);
  assert.ok(spec.ref.tag, 'the chart source needs a version tag');
  assert.equal(dep.currentValue, spec.ref.tag);
  assert.match(dep.currentDigest ?? '', /^sha256:[a-f0-9]{64}$/, 'the chart source needs an immutable digest');
  assert.equal(dep.currentDigest, spec.ref.digest);
}

function nextVersion(value) {
  const prefix = value.startsWith('v') ? 'v' : '';
  const [major, minor, patch] = value.replace(/^v/, '').split('.').map(Number);
  return { newValue: `${prefix}${major}.${minor}.${patch + 1}`, newMajor: major, newMinor: minor };
}

function propose(dep, updateType) {
  const version = updateType === 'patch' ? nextVersion(dep.currentValue) : { newValue: dep.currentValue };
  dep.updates = [{ updateType, newVersion: version.newValue, newDigest, ...version }];
}

async function replaceAll(branch, contents) {
  const updated = { ...contents };
  for (const upgrade of branch.upgrades) {
    assert.equal(upgrade.automerge, false);
    assert.equal(upgrade.pinDigests, true);
    updated[upgrade.packageFile] = await doAutoReplace(upgrade, updated[upgrade.packageFile], false);
  }
  return updated;
}

async function allSources() {
  const contents = Object.fromEntries(sources.map((file) => [file, readFileSync(file, 'utf8')]));
  const flux = [];
  for (const [file, content] of Object.entries(contents)) flux.push(...(await extract(content, file)).flux);
  return { contents, flux };
}

const expected = (content, change) => {
  const value = YAML.parse(content);
  Object.assign(value.spec.ref, change);
  return value;
};

test('every OCI chart source pairs a version tag with a digest and is extracted once', async () => {
  assert.ok(sources.length > 0);
  for (const file of sources) {
    const content = readFileSync(file, 'utf8');
    assertPinned(content, await extract(content, file));
  }
});

test('a re-pushed version becomes a reviewed digest update that changes only the digest', async () => {
  const { contents, flux } = await allSources();
  for (const dep of flux.flatMap((file) => file.deps)) propose(dep, 'digest');
  const { branches } = await branchifyUpgrades(config, { flux });
  assert.equal(branches.flatMap((branch) => branch.upgrades).length, sources.length, 'no digest update may be withheld');
  for (const branch of branches) {
    assert.ok((branch.minimumGroupSize ?? 1) <= branch.upgrades.length, `${branch.branchName} is withheld by its group minimum`);
    const updated = await replaceAll(branch, contents);
    for (const { packageFile } of branch.upgrades) {
      assert.deepEqual(YAML.parse(updated[packageFile]), expected(contents[packageFile], { digest: newDigest }));
      assertPinned(updated[packageFile], await extract(updated[packageFile], packageFile));
    }
  }
});

test('a new chart version replaces the tag and digest together', async () => {
  const { contents, flux } = await allSources();
  for (const dep of flux.flatMap((file) => file.deps)) propose(dep, 'patch');
  const { branches } = await branchifyUpgrades(config, { flux });
  for (const branch of branches) {
    const updated = await replaceAll(branch, contents);
    for (const { packageFile, newValue } of branch.upgrades) {
      assert.deepEqual(YAML.parse(updated[packageFile]), expected(contents[packageFile], { tag: newValue, digest: newDigest }));
      assertPinned(updated[packageFile], await extract(updated[packageFile], packageFile));
    }
  }
});

test('Flux Operator releases stay grouped; digest changes do not join the group', async () => {
  const { flux } = await allSources();
  const members = flux.filter((file) => /flux-(operator|instance)/.test(file.deps[0].depName));
  assert.ok(members.length >= 3);
  for (const file of members) propose(file.deps[0], 'patch');
  const release = await branchifyUpgrades(config, { flux: members });
  assert.equal(release.branches.length, 1);
  assert.equal(release.branches[0].groupName, 'flux-operator');
  assert.equal(release.branches[0].upgrades.length, members.length);

  for (const file of members) {
    propose(file.deps[0], 'digest');
    const digest = await branchifyUpgrades(config, { flux: [file] });
    assert.equal(digest.branches.length, 1);
    assert.ok((digest.branches[0].minimumGroupSize ?? 1) <= 1, 'a lone digest update must not wait for the group');
  }
});

test('external-dns keeps its manual approval hold for digest changes', async () => {
  const { flux } = await allSources();
  const held = flux.filter((file) => file.deps[0].depName.endsWith('/external-dns'));
  assert.equal(held.length, 1);
  propose(held[0].deps[0], 'digest');
  const { branches } = await branchifyUpgrades(config, { flux: held });
  assert.equal(branches[0].upgrades[0].dependencyDashboardApproval, true);
});

test('a new tag-only chart source gets a reviewed digest pin from Renovate', async () => {
  const [file] = sources;
  const pinned = readFileSync(file, 'utf8');
  const tagOnly = pinned.replace(/\n\s+digest: sha256:[a-f0-9]{64}/, '');
  const files = await extract(tagOnly, file);
  const [dep] = deps(files);
  assert.equal(dep.currentDigest, undefined);
  dep.updates = [{ updateType: 'pinDigest', isPinDigest: true, newValue: dep.currentValue, newVersion: dep.currentValue, newDigest }];
  const { branches } = await branchifyUpgrades(config, { flux: files.flux });
  const updated = await replaceAll(branches[0], { [file]: tagOnly });
  assert.equal(updated[file], pinned.replace(/sha256:[a-f0-9]{64}/, newDigest));
});

test('digest pinning stays scoped to Helm chart sources', async () => {
  const file = 'kubernetes/apps/example/app/ocirepository.yaml';
  const content = `apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: manifests
spec:
  interval: 15m
  ref:
    tag: 1.2.3
  url: oci://ghcr.io/example/manifests
`;
  const files = await extract(content, file);
  propose(deps(files)[0], 'patch');
  const { branches } = await branchifyUpgrades(config, { flux: files.flux });
  assert.notEqual(branches[0].upgrades[0].pinDigests, true);
});

test('CI rejects a chart source without a tag or digest', async () => {
  const [file] = sources;
  const pinned = readFileSync(file, 'utf8');
  const tagOnly = pinned.replace(/\n\s+digest: sha256:[a-f0-9]{64}/, '');
  await assert.rejects(async () => assertPinned(tagOnly, await extract(tagOnly, file)), /immutable digest/);
  const digestOnly = pinned.replace(/\n\s+tag: \S+/, '');
  await assert.rejects(async () => assertPinned(digestOnly, await extract(digestOnly, file)), /version tag/);
});
