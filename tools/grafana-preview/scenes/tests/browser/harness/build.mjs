// Bundles the harness with every dependency inlined. Output stays in a scratch directory passed as OUT.
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';

const lane = path.resolve(import.meta.dirname, '../../..');
const out = process.env.OUT ?? '/tmp/scenes-harness';
const shared = path.resolve(lane, '../shared/src');
const homeView = fs.existsSync(`${shared}/HomeView.tsx`) ? `${shared}/HomeView.tsx` : path.resolve(lane, 'tests/support/HomeViewStub.tsx');
// Shared source resolves its imports from this lane, so the harness holds one React and one Grafana.
const oneCopy = {
  name: 'one-copy',
  setup(build) {
    build.onResolve({ filter: /^[^./]/ }, (args) => {
      if (args.pluginData || !args.importer.startsWith(shared)) {
        return undefined;
      }
      return build.resolve(args.path, { resolveDir: lane, kind: args.kind, importer: args.importer, pluginData: true });
    });
  },
};
await build({
  absWorkingDir: lane,
  entryPoints: ['tests/browser/harness/main.tsx'],
  bundle: true,
  outfile: `${out}/main.js`,
  format: 'iife',
  platform: 'browser',
  target: 'es2022',
  jsx: 'automatic',
  sourcemap: false,
  define: { 'process.env.NODE_ENV': '"development"', global: 'globalThis' },
  alias: { '@shared/HomeView': homeView, '@shared': shared },
  plugins: [oneCopy],
  loader: { '.json': 'json', '.css': 'empty', '.svg': 'dataurl', '.woff2': 'dataurl', '.woff': 'dataurl', '.ttf': 'dataurl', '.png': 'dataurl', '.gif': 'dataurl' },
  logLevel: 'error',
});
fs.writeFileSync(`${out}/index.html`, '<!doctype html><html><head><meta charset="utf-8"><title>Scenes host harness</title><style>html,body{margin:0;background:#111217}</style></head><body><div id="root"></div><script src="/main.js"></script></body></html>');
console.log('built', out, fs.statSync(`${out}/main.js`).size, 'using', path.relative(lane, homeView));
