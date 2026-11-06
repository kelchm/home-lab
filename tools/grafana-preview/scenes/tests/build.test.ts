import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

describe('build', () => {
  it('keeps its output directory, because a running Grafana bind-mounts it', () => {
    const out = fs.mkdtempSync(path.join(os.tmpdir(), 'scenes-dist-'));
    const build = () => execFileSync(process.execPath, ['build.mjs'], { env: { ...process.env, OUT: out, REPORT: path.join(out, 'report.json') } });
    build();
    const plugin = JSON.parse(fs.readFileSync(path.join(out, 'plugin.json'), 'utf8'));
    expect(plugin.id).toBe('homelab-observability-app');
    expect(plugin.includes[0].path).toBe('/a/homelab-observability-app/explore');
    const before = fs.statSync(out).ino;
    build();
    expect(fs.statSync(out).ino).toBe(before);
    expect(fs.readdirSync(out).filter((name) => name.endsWith('.tmp'))).toEqual([]);
    fs.rmSync(out, { recursive: true });
  });
});
