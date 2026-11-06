// Bundles the panel to the AMD module Grafana loads. Nothing recorded is an input here: every value arrives as a frame at runtime.
import { build } from 'esbuild';
import fs from 'node:fs';

const external = ['react', 'react-dom', 'react/jsx-runtime', '@grafana/data', '@grafana/runtime'];
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
  define: { 'process.env.NODE_ENV': '"production"' },
});
const code = result.outputFiles[0].text;
const args = external.map((_, i) => `__e${i}`);
const table = external.map((name, i) => `${JSON.stringify(name)}:__e${i}`).join(',');
const amd = `define(${JSON.stringify(external)},function(${args.join(',')}){var __t={${table}};var require=function(n){if(n in __t)return __t[n];throw new Error("missing external "+n)};var module={exports:{}},exports=module.exports;${code}\nreturn module.exports;});`;
// Grafana bind-mounts this directory, so it must never be deleted or replaced: a new directory would leave the running container on the
// old one. Each file is swapped in by rename, so a rebuild reaches a running Grafana and it never serves a half-written module.
fs.mkdirSync(`${out}/img`, { recursive: true });
const publish = (name, content) => {
  fs.writeFileSync(`${out}/${name}.tmp`, content);
  fs.renameSync(`${out}/${name}.tmp`, `${out}/${name}`);
};
publish('module.js', amd);
publish('plugin.json', fs.readFileSync('plugin.json'));
publish('img/logo.svg', fs.readFileSync('img/logo.svg'));
console.log('module.js', Buffer.byteLength(amd), 'bytes');
