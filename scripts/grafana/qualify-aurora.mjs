import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { randomBytes, createHash } from 'node:crypto';
// Optional upgrade qualification. Runtime plugin has no npm dependencies.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
const launch = () => chromium.launch({ headless: true, ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}) });
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const source = path.join(root, 'kubernetes/apps/observability/grafana/app/aurora');
// A home-directory path also works with Colima's default macOS file sharing.
const work = fs.mkdtempSync(path.join(os.homedir(), '.grafana-aurora-'));
const yq = process.env.YQ ?? 'yq', helm = process.env.HELM ?? 'helm', kustomize = process.env.KUSTOMIZE ?? 'kustomize';
const command = (exe, args, input) => execFileSync(exe, args, { encoding: 'utf8', input, maxBuffer: 32 * 1024 * 1024, stdio: ['pipe', 'pipe', 'pipe'] });
const parseYaml = (text) => JSON.parse(command(yq, ['eval-all', '-o=json', '-I=0', '[.]', '-'], text));
const resources = parseYaml(command(kustomize, ['build', path.dirname(source)]));
const release = resources.find((r) => r.kind === 'HelmRelease');
const config = resources.find((r) => r.kind === 'ConfigMap' && r.metadata.name.startsWith('grafana-aurora-plugin-'));
assert.ok(config, 'Aurora needs a hashed ConfigMap, independently of stable dashboard names');
const mounts = release.spec.values.extraConfigmapMounts;
assert.ok(mounts.every((m) => m.configMap === config.metadata.name), 'Every mount must follow the hashed reference');
assert.equal(release.spec.values.image, undefined, 'Keep the official chart image');
const valuesFile = path.join(work, 'values.json');
fs.writeFileSync(valuesFile, JSON.stringify(release.spec.values));
let chart = process.env.GRAFANA_CHART;
if (!chart) {
  const repository = resources.find((r) => r.kind === 'HelmRepository');
  command(helm, ['pull', 'grafana', '--repo', repository.spec.url, '--version', release.spec.chart.spec.version, '--untar', '--untardir', work]);
  chart = path.join(work, 'grafana');
}
const rendered = parseYaml(command(helm, ['template', 'grafana', chart, '-n', 'observability', '-f', valuesFile]));
const pod = rendered.find((r) => r.kind === 'Deployment').spec.template.spec;
const container = pod.containers.find((c) => c.name === 'grafana');
for (const mount of mounts) {
  const volume = pod.volumes.find((v) => v.name === mount.name);
  const actual = container.volumeMounts.find((m) => m.name === mount.name);
  assert.equal(volume.configMap.name, config.metadata.name);
  assert.equal(actual.mountPath, mount.mountPath);
  assert.equal(actual.subPath, mount.subPath);
  assert.equal(actual.readOnly, true);
}
const chartConfig = rendered.find((r) => r.kind === 'ConfigMap' && r.data?.['grafana.ini']).data;
assert.match(chartConfig['grafana.ini'], /allow_loading_unsigned_plugins = homelab-aurora-app/);
assert.match(chartConfig['grafana.ini'], /grafana.visualDesignRefresh = true/);
const docker = process.env.DOCKER ?? 'docker';
const current = process.env.GRAFANA_IMAGE ?? 'grafana/grafana:13.3.0-distroless@sha256:540b8afb775bf34aefce417e4329039daa5d44b6f2fbda6881397b1b247ca7df';
const adjacent = 'grafana/grafana:13.2.5-distroless@sha256:7bd4a6c9fa5425e722654c56f4fc8404ed042a89542fdb6d963177b6d80497ec';
const name = 'grafana-stock-theme-' + randomBytes(4).toString('hex');
let volume = name + '-data'; const volumes = new Set([volume]);
const password = randomBytes(20).toString('hex');
const output = process.env.GRAFANA_THEME_EVIDENCE ?? fs.mkdtempSync(path.join(os.tmpdir(), 'grafana-aurora-evidence-')); fs.mkdirSync(output, { recursive: true });
const provisioning = path.join(work, 'provisioning');
for (const dir of ['datasources', 'plugins']) fs.mkdirSync(path.join(provisioning, dir), { recursive: true });
fs.writeFileSync(path.join(provisioning, 'datasources/testdata.yaml'), 'apiVersion: 1\ndatasources:\n  - name: TestData\n    uid: testdata\n    type: grafana-testdata-datasource\n    access: proxy\n');
fs.writeFileSync(path.join(provisioning, 'plugins/apps.yaml'), config.data['apps.yaml']);
const iniFile = path.join(work, 'grafana.ini');
const ini = release.spec.values['grafana.ini'];
fs.writeFileSync(iniFile, ['plugins', 'feature_toggles'].map((section) => `[${section}]\n` + Object.entries(ini[section]).map(([k, v]) => `${k} = ${v}`).join('\n')).join('\n'));
const run = (args) => {
  try { return execFileSync(docker, args, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] }).trim(); }
  catch (error) { throw new Error(`docker ${args[0]} failed: ${error.stderr ?? error.code}`); }
};
const auth = { authorization: `Basic ${Buffer.from('admin:' + password).toString('base64')}`, 'content-type': 'application/json' };
const report = { checks: [], pageErrors: [], queryStatuses: [], warnings: [], moduleLoads: [], packaging: { configMap: config.metadata.name, chartImage: container.image, moduleVersion: JSON.parse(config.data['plugin.json']).info.version } };
const check = (name, detail = {}) => { report.checks.push({ name, ...detail }); console.log(name); };
let base, port, browser, page, mode;
function projection(mode) {
  // Kubelet's projected-volume symlinks, with only the two plugin keys exposed.
  const dir = path.join(work, mode);
  const payload = path.join(dir, '..2026_11_03');
  const pluginMount = mounts.find((m) => m.name === 'aurora-plugin');
  fs.mkdirSync(path.join(payload, pluginMount.subPath), { recursive: true });
  let module = config.data['module.js'];
  if (mode === 'missing-api') module = module.replace('typeof data.createTheme', 'typeof data.missingThemeFactory');
  if (mode === 'bad-theme') module = module.replace('data.createTheme(definition)', 'data.createTheme({ ...definition, colors: null })');
  if (mode === 'unsupported') module = module.replace("const supported = ['13.3.0'];", 'const supported = [];');
  if (mode === 'cache-probe') module += '\n// Cache-version qualification probe.\n';
  const manifest = JSON.parse(config.data['plugin.json']);
  manifest.info.version = '0.1.0-' + createHash('sha256').update(module).digest('hex').slice(0, 12);
  for (const item of pluginMount.items) {
    fs.writeFileSync(path.join(payload, item.path), item.key === 'module.js' ? module : JSON.stringify(manifest));
  }
  fs.symlinkSync('..2026_11_03', path.join(dir, '..data'));
  fs.symlinkSync('..data/' + pluginMount.subPath, path.join(dir, pluginMount.subPath));
  return { dir: path.join(dir, pluginMount.subPath), module, version: manifest.info.version };
}
let selected;

async function start(nextMode, image = current, fresh = false) {
  if (page) await page.goto('about:blank');
  try { fs.writeFileSync(path.join(output, `${mode}.log`), run(['logs', name])); } catch {}
  try { run(['rm', '-f', name]); } catch {}
  mode = nextMode;
  if (fresh) { volume = name + '-adjacent-data'; volumes.add(volume); }
  selected = projection(mode);
  run(['volume', 'create', '--label', 'research.homelab.aurora=true', volume]);
  run(['run', '-d', '--name', name, '--label', 'research.homelab.aurora=true', '--read-only', '--tmpfs', '/tmp:mode=1777', '--cap-drop', 'ALL', '--memory', '1g',
    '-p', `127.0.0.1:${port ?? ''}:3000`, '-v', `${volume}:/var/lib/grafana`,
    '-v', `${path.join(provisioning, 'datasources/testdata.yaml')}:/etc/grafana/provisioning/datasources/testdata.yaml:ro`,
    '-v', `${path.join(provisioning, 'plugins/apps.yaml')}:/etc/grafana/provisioning/plugins/aurora.yaml:ro`,
    '-v', `${selected.dir}:/var/lib/grafana/plugins/homelab-aurora-app:ro`, '-v', `${iniFile}:/etc/grafana/grafana.ini:ro`,
    '-e', 'GF_PATHS_PLUGINS=/var/lib/grafana/plugins', '-e', nextMode === 'normal' && !fresh ? `GF_PLUGINS_PREINSTALL_SYNC=${chartConfig.plugins}` : 'GF_PLUGINS_PREINSTALL_DISABLED=true',
    '-e', 'GF_AUTH_ANONYMOUS_ENABLED=true', '-e', 'GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer', '-e', `GF_SECURITY_ADMIN_PASSWORD=${password}`,
    '-e', 'GF_USERS_DEFAULT_THEME=dark', '-e', 'GF_SECURITY_ASSET_SRI_CHECKS_ENABLED=true',
    '-e', 'GF_ANALYTICS_REPORTING_ENABLED=false', '-e', 'GF_ANALYTICS_CHECK_FOR_UPDATES=false', '-e', 'GF_NEWS_NEWS_FEED_ENABLED=false', image]);
  port = run(['port', name, '3000/tcp']).match(/:(\d+)$/)[1]; base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch(base + '/api/health')).ok) return; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Stock Grafana did not start');
}
async function api(url, method = 'GET', body) {
  const r = await fetch(base + url, { method, headers: auth, body: body === undefined ? undefined : JSON.stringify(body) });
  assert.ok(r.ok, `${url}: ${r.status}`); return r.json();
}
const readTheme = () => page.evaluate(async () => { const { config } = await System.import('@grafana/runtime'); return { name: config.theme2.name, canvas: config.theme2.colors.background.canvas, palette: config.theme2.visualization.palette, version: config.buildInfo.version, body: getComputedStyle(document.body).backgroundColor }; });
async function open(aurora, expectModule = true) {
  await page.goto(base + '/d/stock-theme-proof/proof', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!document.querySelector('.uplot canvas'));
  if (aurora) await page.waitForFunction(async () => (await System.import('@grafana/runtime')).config.theme2.name === 'Homelab Aurora');
  await page.waitForLoadState('networkidle');
  // Check actual served bytes and Grafana's versioned module URL (including warm-cache rollbacks).
  const moduleUrl = await page.evaluate(() => [...performance.getEntriesByType('resource')].map((r) => r.name).find((url) => url.includes('homelab-aurora-app/module.js')));
  if (expectModule) assert.ok(moduleUrl, 'The enabled app module must actually load');
  if (moduleUrl) {
    assert.ok(moduleUrl.includes(selected.version), moduleUrl);
    assert.equal(await (await page.request.get(moduleUrl)).text(), selected.module);
    report.moduleLoads.push({ mode, version: selected.version, sha256: createHash('sha256').update(selected.module).digest('hex') });
  }
  const result = await readTheme(); assert.equal(result.name === 'Homelab Aurora', aurora); return result;
}
async function select(id) {
  await page.evaluate(async (id) => { const data = await System.import('@grafana/data'), runtime = await System.import('@grafana/runtime'); runtime.getAppEvents().publish(new runtime.ThemeChangedEvent(data.getThemeById(id))); }, id);
  // Publishing is synchronous; ThemeProvider's React update is not. Avoid reading the old theme.
  await page.waitForTimeout(500);
  await page.waitForFunction(async (id) => { const name = (await System.import('@grafana/runtime')).config.theme2.name; return id === 'dark' ? name === 'Homelab Aurora' : id === 'light' ? name.includes('Light') : name === 'Tron'; }, id);
  await page.waitForTimeout(500);
  const actual = await readTheme();
  assert.ok(id === 'dark' ? actual.name === 'Homelab Aurora' : id === 'light' ? actual.name.includes('Light') : actual.name === 'Tron', JSON.stringify(actual));
}
const dashboard = { uid: 'stock-theme-proof', title: 'Stock Grafana theme proof', schemaVersion: 39, panels: [{ id: 1, type: 'timeseries', title: 'Native chart', gridPos: { x: 0, y: 0, w: 24, h: 12 }, datasource: { uid: 'testdata', type: 'grafana-testdata-datasource' }, targets: [{ refId: 'A', scenarioId: 'random_walk', seriesCount: 3 }], fieldConfig: { defaults: { color: { mode: 'palette-classic' }, custom: { lineWidth: 2 } }, overrides: [] } }] };
try {
  check('Kustomize and the deployed Helm chart resolve read-only hashed mounts');
  await start('normal');
  const startupLog = run(['logs', name]);
  assert.doesNotMatch(startupLog, /Could not (?:register|initialize) plugin.*homelab-aurora-app/);
  const installed = await api('/api/plugins');
  const datasourcePlugins = installed.filter((p) => ['victoriametrics-metrics-datasource', 'victoriametrics-logs-datasource'].includes(p.id));
  assert.equal(datasourcePlugins.length, 2);
  check('Production datasource plugins coexist with the ConfigMap-mounted adapter', { plugins: datasourcePlugins.map((p) => ({ id: p.id, version: p.info.version })) });
  await api('/api/dashboards/db', 'POST', { dashboard, overwrite: true });
  browser = await launch(); page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 } })).newPage();
  page.on('pageerror', (e) => report.pageErrors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'warning' && m.text().includes('Aurora')) report.warnings.push({ mode, message: m.text() }); });
  page.on('response', (r) => { if (r.url().includes('/api/ds/query')) report.queryStatuses.push(r.status()); });
  check('Official stock Grafana applies Aurora through events alone', { theme: await open(true) });
  const pixels = await page.evaluate((colors) => {
    const counts = Object.fromEntries(colors.map((c) => [c.toLowerCase(), 0]));
    for (const canvas of document.querySelectorAll('.uplot canvas')) {
      const ctx = canvas.getContext('2d'); if (!ctx) continue;
      const bytes = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < bytes.length; i += 4) {
        if (!bytes[i + 3]) continue;
        const hex = '#' + [bytes[i], bytes[i + 1], bytes[i + 2]].map((n) => n.toString(16).padStart(2, '0')).join('');
        if (hex in counts) counts[hex]++;
      }
    }
    return counts;
  }, ['#4DB0FF', '#3FE6C4', '#C685FF']);
  assert.ok(Object.values(pixels).every((n) => n > 10), JSON.stringify(pixels)); check('Native chart pixels use all three Aurora colors', { pixels });
  await page.screenshot({ path: path.join(output, 'event-only-desktop.png') });
  for (const id of ['dark', 'light', 'dark', 'tron', 'dark']) { await select(id); check(`Runtime ${id} selection is respected`, { theme: await readTheme() }); }
  // Exercise Grafana's actual toggleTheme/changeTheme service and CSS swap,
  // in addition to the direct events above. c r is its runtime-only shortcut.
  for (const expected of ['Light', 'Homelab Aurora']) {
    await page.keyboard.press('Escape'); await page.keyboard.press('c'); await page.keyboard.press('r');
    await page.waitForFunction(async (expected) => (await System.import('@grafana/runtime')).config.theme2.name.includes(expected), expected);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(500);
    assert.ok((await readTheme()).name.includes(expected));
  }
  check('Grafana keyboard shortcuts switch theme through the real service and CSS-loading path');
  const cdp = await page.context().newCDPSession(page); await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
  for (let i = 0; i < 5; i++) await open(true);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 }); check('Five throttled stock-image reloads apply Aurora');
  await api('/api/plugins/homelab-aurora-app/settings', 'POST', { enabled: false, pinned: false }); check('Disabling the app restores native styling and a working chart', { theme: await open(false, false) });
  const adminContext = await browser.newContext();
  const login = await adminContext.request.post(base + '/login', { data: { user: 'admin', password } });
  assert.ok(login.ok(), 'Temporary local administrator can sign in');
  const adminPage = await adminContext.newPage();
  await adminPage.goto(base + '/plugins/homelab-aurora-app', { waitUntil: 'networkidle' });
  await adminPage.waitForFunction(() => performance.getEntriesByType('resource').some((r) => r.name.includes('homelab-aurora-app/module.js')));
  await adminPage.waitForTimeout(3500);
  assert.notEqual(await adminPage.evaluate(async () => (await System.import('@grafana/runtime')).config.theme2.name), 'Homelab Aurora');
  check('Opening the disabled app administration page imports its module without applying Aurora');
  await adminContext.close();
  await api('/api/plugins/homelab-aurora-app/settings', 'POST', { enabled: true, pinned: false }); await open(true); check('Re-enabling the app restores Aurora');
  await start('cache-probe'); await open(true);
  await start('restored'); await open(true);
  check('Changed-version module and original module both load in the same browser cache and database');
  for (const next of ['missing-api', 'bad-theme', 'unsupported']) { await start(next); check(`${next} falls back with a working native chart`, { theme: await open(false) }); }
  await start('adjacent', adjacent, true); await api('/api/dashboards/db', 'POST', { dashboard, overwrite: true });
  check('Unqualified 13.2.5 stays on native styling with a working chart', { theme: await open(false) });
  assert.deepEqual(report.pageErrors, []); assert.ok(report.queryStatuses.length > 0 && report.queryStatuses.every((n) => n === 200)); report.status = 'passed';
} catch (error) { report.status = 'failed'; report.error = String(error); await page?.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {}); throw error;
} finally {
  await browser?.close();
  try { fs.writeFileSync(path.join(output, `${mode}.log`), run(['logs', name])); } catch {}
  try { run(['rm', '-f', name]); } catch {}
  for (const volume of volumes) { try { run(['volume', 'rm', volume]); } catch {} }
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  fs.rmSync(work, { recursive: true, force: true });
  console.log(`Evidence: ${output}`);
}
