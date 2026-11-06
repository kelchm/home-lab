// Bundles the app to the AMD module Grafana loads. Grafana 13.3 does not share @grafana/scenes with plugins, so it ships in module.js.
// shared/src is bundled from source, which is what keeps one renderer across both hosts.
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';

const external = ['react', 'react-dom', 'react/jsx-runtime', 'react-dom/client', 'react-router', 'rxjs', 'rxjs/operators', 'lodash', 'moment', 'i18next',
  '@emotion/css', '@emotion/react', '@grafana/data', '@grafana/runtime', '@grafana/ui', 'react-inlinesvg'];
const out = process.env.OUT ?? 'dist';
const result = await build({
  entryPoints: ['src/module.tsx'],
  bundle: true,
  write: false,
  minify: true,
  format: 'cjs',
  platform: 'browser',
  target: 'es2022',
  jsx: 'automatic',
  external,
  alias: { '@shared': path.resolve('../shared/src') },
  define: { 'process.env.NODE_ENV': '"production"' },
  metafile: true,
  loader: { '.json': 'json', '.woff2': 'dataurl' },
});
const code = result.outputFiles[0].text;
const args = external.map((_, i) => `__e${i}`);
const table = external.map((name, i) => `${JSON.stringify(name)}:__e${i}`).join(',');
const amd = `define(${JSON.stringify(external)},function(${args.join(',')}){var __t={${table}};var require=function(n){if(n in __t)return __t[n];throw new Error("missing external "+n)};var module={exports:{}},exports=module.exports;${code}\nreturn module.exports;});`;
// Grafana bind-mounts this directory, so it must never be deleted or replaced: a new directory would leave the running container on the
// old one. Each file is swapped in by rename, so a rebuild reaches a running Grafana and it never serves a half-written module.
fs.mkdirSync(out, { recursive: true });
const publish = (name, content) => {
  fs.writeFileSync(`${out}/${name}.tmp`, content);
  fs.renameSync(`${out}/${name}.tmp`, `${out}/${name}`);
};
publish('module.js', amd);
publish('plugin.json', fs.readFileSync('plugin.json'));
const inputs = {};
for (const output of Object.values(result.metafile.outputs)) {
  for (const [file, info] of Object.entries(output.inputs)) {
    const name = file.match(/node_modules\/\.pnpm\/([^/]+)/)?.[1]?.replace(/@\d.*$/, '') ?? (file.includes('shared/') ? 'shared' : file.includes('runtime/') ? 'queries.json' : 'scenes-src');
    inputs[name] = (inputs[name] ?? 0) + info.bytesInOutput;
  }
}
const top = Object.entries(inputs).sort((a, b) => b[1] - a[1]).slice(0, 14);
fs.writeFileSync(process.env.REPORT ?? 'bundle-report.json', JSON.stringify({ moduleBytes: Buffer.byteLength(amd), top }, null, 2));
console.log('module.js', Buffer.byteLength(amd), 'bytes');
console.log(top);
