import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { build } = await import(pathToFileURL(path.join(root, 'shared/node_modules/esbuild/lib/main.js')));
const names = ['kubernetes', 'proxmox', 'sparks', 'athena', 'network', 'power', 'devices'];
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'grafana-preview-contracts-'));

try {
  const result = await build({
    stdin: {
      contents: names.map((name, i) => `import { domain as d${i} } from './shared/src/domains/${name}/index.ts';`).join('\n') + `\nexport const domains = [${names.map((_, i) => `d${i}`).join(',')}];`,
      resolveDir: root,
      sourcefile: 'contract-check.ts',
      loader: 'ts',
    },
    bundle: true,
    platform: 'node',
    format: 'esm',
    outfile: path.join(temp, 'domains.mjs'),
    metafile: true,
    logLevel: 'silent',
  });
  assert(!Object.keys(result.metafile.inputs).some((input) => input.includes('/fixtures/')), 'Recorded samples must not be bundled into the application');
  const { domains } = await import(pathToFileURL(path.join(temp, 'domains.mjs')));
  const focuses = new Set(domains.map((domain) => domain.focus));
  const byFocus = new Map(domains.map((domain) => [domain.focus, domain]));
  const globalRefs = new Set();
  const checks = [];
  const timeRange = { from: new Date(1793496424636), to: new Date(1793582824636), raw: { from: 1793496424636, to: 1793582824636 } };
  assert.equal(focuses.size, 7, 'Every system must have its own focus');
  for (const domain of domains) {
    assert(domain.pages.length >= 2, `${domain.focus} needs useful dashboard depth beyond a summary`);
    const pageIds = new Set(domain.pages.map((page) => page.id));
    assert.equal(pageIds.size, domain.pages.length, `${domain.focus}: duplicate page ids`);
    const refs = new Set(domain.queries.map((query) => query.refId));
    assert.equal(refs.size, domain.queries.length, `${domain.focus}: duplicate query references`);
    for (const query of domain.queries) {
      assert(query.expr.trim(), `${domain.focus}/${query.refId}: empty expression`);
      assert(!/\$(?!__)[a-zA-Z]/.test(query.expr), `${domain.focus}/${query.refId}: unresolved dashboard variable in a standalone query`);
      assert(!globalRefs.has(query.refId), `Query ref ${query.refId} is shared across independent domains`);
      globalRefs.add(query.refId);
      assert(query.instant !== query.range, `${query.refId}: declare exactly one query mode`);
    }
    const href = (route = {}) => {
      const target = route.focus ?? domain.focus;
      assert(focuses.has(target) || target === 'Overview', `Unknown destination ${target}`);
      if (route.view && target !== 'Overview') assert(byFocus.get(target).pages.some((page) => page.id === route.view), `${target}: missing internal view ${route.view}`);
      const url = new URL('http://preview.invalid/a/homelab-observability-app/explore');
      for (const [key, value] of Object.entries({ source: 'replay', focus: target, from: timeRange.raw.from, to: timeRange.raw.to, ...route })) {
        if (value != null) url.searchParams.set(key, String(value));
      }
      return url.href;
    };
    for (const page of domain.pages) {
      assert(page.title && page.question, `${domain.focus}/${page.id}: state its operational question`);
      for (const ref of page.required) assert(refs.has(ref) || /^[HMR]_/.test(ref), `${domain.focus}/${page.id}: undeclared required ref ${ref}`);
      for (const entity of [null, 'not-a-real-entity', 'spark-1 / + ? &']) {
        const sections = page.sections({
          series: [], timeRange, mode: 'recorded', timeZone: 'America/New_York', entity,
          coverage: { freshness: 'unavailable', missing: page.required.length, retained: 0, retainedAt: null, unanswered: true },
          href, deeper: (uid, variables = {}) => `http://preview.invalid/d/${encodeURIComponent(uid)}?${new URLSearchParams({ from: String(timeRange.raw.from), to: String(timeRange.raw.to), ...variables })}`,
        });
        assert(sections.length, `${domain.focus}/${page.id}: empty composition`);
        assert.equal(new Set(sections.map((section) => section.id)).size, sections.length, `${domain.focus}/${page.id}: duplicate section ids`);
        for (const section of sections) {
          if (section.kind === 'chart') for (const ref of section.refs) assert(refs.has(ref) || /^[HMR]_/.test(ref), `${domain.focus}/${page.id}: chart uses undeclared ref ${ref}`);
          if (section.kind === 'table') {
            assert(section.empty, `${domain.focus}/${page.id}: table needs an honest empty state`);
            for (const row of section.rows) assert.equal(row.cells.length, section.columns.length, `${domain.focus}/${page.id}: table cell count mismatch`);
          }
          if (section.kind === 'metrics') for (const item of section.items) assert(!/^(?:NaN|Infinity|undefined)(?:\b|$)/.test(item.value), `${domain.focus}/${page.id}: invalid missing-value formatting`);
        }
      }
      checks.push(`${domain.focus}/${page.id}: unknown data and invalid entity stay renderable`);
    }
  }
  process.stdout.write(JSON.stringify({ status: 'passed', domains: domains.length, pages: checks.length, queryReferences: globalRefs.size, checks }, null, 2) + '\n');
} finally {
  await fs.rm(temp, { recursive: true, force: true });
}
