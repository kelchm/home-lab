import fs from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildHome } from '../src/model/home';
import { briefingOf, statusOf } from '../src/model/status';
import type { FrameLike } from '../src/model/semantics';

const catalog = JSON.parse(fs.readFileSync(new URL('../../runtime/catalog.json', import.meta.url), 'utf8'));
const snapshot = JSON.parse(fs.readFileSync(new URL('../../fixtures/reference/overview-snapshot.json', import.meta.url), 'utf8'));
const frames: FrameLike[] = catalog.filter((e: { scope: string }) => e.scope === 'common').flatMap((e: any) => e.series.map((s: any) => ({ refId: e.refId, fields: [{ name: 'Time', type: 'time', values: s.t, config: { interval: s.step } }, { name: 'Value', type: 'number', values: s.v, labels: s.labels, config: {} }] })));
const model = () => buildHome(frames, snapshot.from, snapshot.to, { missing: 0, retained: 0, total: 40 });
describe('captured model parity and coverage', () => {
 it('matches accepted review counts and grouping', () => {
  const r = model().review!;
  for (const key of ['action', 'recovered', 'observe', 'maintenance'] as const) expect(r[key]).toHaveLength(snapshot.review[key].length);
 });
 it('preserves all thirteen hosts with their platform identity', () => {
  const h = model().hosts;expect(h).toHaveLength(13);expect(new Set(h.map(x => x.platform + '/' + x.instance)).size).toBe(13);
 });
 it('preserves all ten configured check assertions', () => {
  const checks = model().checks;expect(checks).toHaveLength(10);expect(checks.find(x => x.id === 'k8s-gateway')?.assertion).toMatch(/resolves/);
 });
 it('maps the recovered critical DSM event to Athena', () => {
  const row = model().review!.recovered.find(x => x.probe === 'athena-dsm');expect(row?.system).toBe('nas');expect(row?.level).toBeGreaterThanOrEqual(3);
 });
 it('does not call incomplete review coverage quiet', () => {
  const m = buildHome(frames, snapshot.from, snapshot.to, { missing: 1, retained: 0, total: 40 });
  expect(briefingOf(m, true, false, '').state).toBe('Coverage incomplete');expect(statusOf(m.review, 'net', true, false).text).not.toBe('Nothing flagged');
 });
});
