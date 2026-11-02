// Use the candidate release's own Zod schema and factory, after its immutable dependency install.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const source = path.resolve(process.argv[2]);
const require = createRequire(path.join(source, 'package.json'));
const { build } = require('esbuild');
const schemaFile = path.join(source, 'packages/grafana-data/src/themes/createTheme.ts');
const themeFile = path.join(source, 'packages/grafana-data/src/themes/themeDefinitions/homelab_aurora.json');
const result = await build({
  stdin: { contents: `
    import assert from 'node:assert/strict';
    import { NewThemeOptionsSchema, createTheme } from ${JSON.stringify(schemaFile)};
    import definition from ${JSON.stringify(themeFile)};
    const parsed = NewThemeOptionsSchema.parse(definition);
    assert.deepEqual(parsed, definition, 'Theme contains fields the upstream schema drops');
    const theme = createTheme(parsed);
    assert.equal(theme.name, 'Homelab Aurora');
    assert.equal(theme.colors.background.canvas, definition.colors.background.canvas);
    assert.deepEqual(theme.visualization.palette, definition.visualization.palette);
    console.log('Upstream theme schema and factory passed');
  `, resolveDir: source },
  bundle: true, platform: 'node', format: 'cjs', write: false,
  conditions: ['@grafana-app/source'],
});
const output = '/tmp/homelab-theme-validator.cjs';
fs.writeFileSync(output, result.outputFiles[0].contents);
const validation = spawnSync(process.execPath, [output], { stdio: 'inherit' });
assert.equal(validation.status, 0, 'Upstream theme validation failed');
