import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

describe('build', () => {
  it('preserves unrelated output files across an incremental build', () => {
    const out = fs.mkdtempSync(path.join(os.tmpdir(), 'scenes-dist-'));
    try {
      const build = () => execFileSync(process.execPath, ['build.mjs'], { env: { ...process.env, OUT: out, REPORT: path.join(out, 'report.json') } });
      build();
      const plugin = JSON.parse(fs.readFileSync(path.join(out, 'plugin.json'), 'utf8'));
      expect(plugin.id).toBe('homelab-observability-app');
      expect(plugin.includes[0].path).toBe('/a/homelab-observability-app/explore');
      const sentinel = path.join(out, 'unrelated.txt');
      fs.writeFileSync(sentinel, 'preserved across builds');
      build();
      expect(fs.readFileSync(sentinel, 'utf8')).toBe('preserved across builds');
      expect(fs.readdirSync(out).filter((name) => name.endsWith('.tmp'))).toEqual([]);
    } finally { fs.rmSync(out, { recursive: true, force: true }); }
  });
});
