import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, test } from 'vitest';
import type { DataFrame } from '@grafana/data';
import { combine, measured, scoped } from '../src/model/adapter';
import { MAY_BE_ABSENT, NEEDS, QUERIES } from '../src/model/catalog';
import { buildHome, sampleAt, valueAtEnd, type Lane } from '../src/model/home';
import { buildNetwork } from '../src/model/network';
import { seriesOf, withGaps, type FrameLike } from '../src/model/semantics';
import { briefingOf, statusOf } from '../src/model/status';
import { runsOf } from '../src/ui/LanePlot';
import { allFrames, catalog, emptyFrame, frame, FROM, REQUESTED_STEP, snapshot, TO } from './fixtures';

const noGaps = { missing: 0, retained: 0, total: 40 };
/** Everything a mounted Network focus holds, for the network model. */
const everything = allFrames() as unknown as FrameLike[];
/** What Overview's views actually receive. */
const series = allFrames(['common']) as unknown as FrameLike[];
const model = buildHome(series, FROM, TO, noGaps);
const review = model.review!;
const without = (...refs: string[]) => series.filter((f) => !refs.includes(f.refId!));
const replace = (ref: string, ...next: DataFrame[]) => [...without(ref), ...(next as unknown as FrameLike[])];
const homeOf = (list: FrameLike[], gaps = noGaps) => buildHome(list, FROM, TO, gaps);

describe('parity with the accepted page', () => {
  const key = (x: { id: string; label: string; level: number; system: string; start?: number | null; end?: number | null; duration?: number | null; spans: number[][] }) => ({ id: x.id, label: x.label, level: x.level, system: x.system, start: x.start ?? null, end: x.end ?? null, duration: x.duration ?? null, spans: x.spans });

  test.each(['action', 'recovered', 'observe', 'maintenance'] as const)('%s rows match, in order, with time spans', (group) => {
    expect(review[group].map(key)).toEqual(snapshot.review[group].map(key));
  });

  test('the headline numbers are the accepted 0 action, 5 resolved, 3 notices, 1 maintenance', () => {
    expect([review.action.length, review.recovered.length, review.observe.length, review.maintenance.length, review.confirmed]).toEqual([0, 5, 3, 1, true]);
  });

  test('every check keeps its state, status code and failure bands', () => {
    expect(model.checks.map((c) => [c.id, c.value, c.http, c.bands])).toEqual(snapshot.checks.map((c: { id: string; ok: number; http: number | null; bands: number[][] }) => [c.id, c.ok, c.http, c.bands]));
  });

  test('both paths match, with their state runs', () => {
    expect(model.wans.map((w) => [w.name, w.value, w.bands])).toEqual(snapshot.wan.map((w: { name: string; value: number; bands: number[][] }) => [w.name, w.value, w.bands]));
  });

  test('all 13 machines are present and match', () => {
    const rows = (list: Array<[string, number | null]>) => [...list].sort((a, b) => a[0].localeCompare(b[0]));
    expect(rows(model.hosts.map((h) => [h.instance, h.up]))).toEqual(rows(snapshot.hosts.map((h: { name: string; up: number }) => [h.name, h.up])));
  });

  test('network devices and radios match in the accepted order', () => {
    const network = buildNetwork(everything, model.wans);
    const round = (v: number | null) => Math.round((v ?? 0) * 1000) / 1000;
    expect(network.devices.map((d) => [d.name, d.clients]).sort()).toEqual(snapshot.systems.net.devices.map((d: { name: string; clients: number }) => [d.name, d.clients]).sort());
    expect(network.radios.map((r) => round(r.busy))).toEqual(snapshot.systems.net.radios.map((r: { busy: number }) => r.busy));
    expect(network.paths.map((p) => [p.title, p.port, p.availability, p.down, p.up])).toEqual(snapshot.wan.map((w: { name: string; availability: number; down: number; up: number }) => [w.name.split(' ')[0], /\((.*)\)/.exec(w.name)![1], w.availability, w.down, w.up]));
    expect(network.tmobile).toMatchObject({ bars: 4, rsrp: -90, sinr: 23, registered: 1 });
  });

  test('the Athena lane carries the outage the accepted page leads with', () => {
    const athena = model.lanes.find((l) => l.system === 'nas')!;
    expect(athena.label).toBe('DSM check failed');
    expect(athena.marks.crit.length).toBeGreaterThan(0);
  });

  test('the vendored model is byte-identical to the reviewed sources', () => {
    const body = ['../fixtures/reference/adapters.js', '../fixtures/reference/home-model.js'].map((f) => readFileSync(f, 'utf8')).join('\n');
    const vendored = readFileSync('src/model/semantics.js', 'utf8');
    const hash = (text: string) => createHash('sha256').update(text).digest('hex');
    expect(hash(vendored.slice(vendored.indexOf(body.slice(0, 40)), vendored.lastIndexOf('\nexport {')))).toBe(hash(body));
  });
});

describe('the catalog contract', () => {
  const known = new Set(catalog.map((e) => e.refId));

  test('every ref the views rest on exists under its exact name', () => {
    expect(Object.values(NEEDS).flat().filter((ref) => !known.has(ref))).toEqual([]);
  });

  test('anything resolved but missing from the catalog is a declared optional upgrade', () => {
    expect(Object.values(QUERIES).flat().filter((ref) => !known.has(ref) && !MAY_BE_ABSENT.has(ref))).toEqual([]);
  });

  test('labels match the series actually loaded', () => {
    const metric = (system: string, list = model) => list.lanes.find((l) => l.system === system)!.metric;
    expect([metric('nas'), metric('net')]).toEqual(['Disk read', 'WAN receive, both ports']);
    const upgraded = homeOf([...series, frame('R_nas_written', [1, 2, 3]), frame('R_net_wanTx', [1, 2, 3], { port: 'eth8' }), frame('R_ups_runtimeMin', [8, 8, 8], { ups: 'compute-rack' })]);
    expect([metric('nas', upgraded), metric('net', upgraded), upgraded.notices]).toEqual(['Disk read + write', 'WAN traffic, both ports', true]);
    expect(model.notices).toBe(false);
  });
});

describe('gaps stay open', () => {
  const t = (i: number) => TO - (9 - i) * 300000;

  test('a replayed 1 min series labelled 5 min keeps 1 min spans, and a 15 min one labelled 5 min is not shattered', () => {
    const step = (ref: string) => seriesOf(measured(series), ref)[0].step;
    expect([step('H_PH'), step('M_E')]).toEqual([60000, 900000]);
    expect(frameStepBefore('H_PH')).toBe(REQUESTED_STEP);
  });

  test('a missing stretch breaks a lane in two rather than joining across it', () => {
    const times = [0, 1, 2, 7, 8, 9].map(t);
    const lanes = homeOf(replace('R_k8s_requests', frame('R_k8s_requests', [1, 2, 3, 4, 5, 6], {}, times))).lanes;
    const lane = lanes.find((l) => l.system === 'k8s')!;
    expect(runsOf(lane.points).map((run) => run.length)).toEqual([3, 3]);
  });

  test('a lone sample between gaps stays a separate run', () => {
    const lane = withGaps(seriesOf(measured([frame('X', [1, 2, 3, 4], {}, [0, 1, 5, 9].map(t))] as unknown as FrameLike[]), 'X')[0]);
    expect(runsOf(lane).map((run) => run.length)).toEqual([2, 1, 1]);
  });

  test('two samples across a hole keep the reported step, because two can\'t establish a cadence', () => {
    const points = withGaps(seriesOf(measured([frame('X', [1, 2], {}, [t(0), t(9)])] as unknown as FrameLike[]), 'X')[0]);
    expect(runsOf(points).length).toBe(2);
  });

  test('a combined lane is null wherever any input is missing', () => {
    expect(combine([[[0, 1], [1, 2]], [[0, 3]]], ([a, b]) => a + b)).toEqual([[0, 4], [1, null]]);
    expect(combine([[[0, 1]], []], ([a]) => a)).toEqual([]);
  });

  test('losing one expected host leaves that lane empty rather than quietly meaning something else', () => {
    const missing = series.filter((f) => !(f.refId === 'M_E' && JSON.stringify(f.fields).includes('pve-sbx-2')));
    expect(homeOf(missing).lanes.find((l) => l.system === 'pve')!.points).toEqual([]);
  });

  test('a failed sample inside a check history stays a failure, and a hole stays a hole', () => {
    const history = frame('H_PH', [1, 0, 1, 1], { probe: 'kanidm' }, [TO - 600000, TO - 540000, TO - 480000, TO - 60000]);
    const bands = homeOf(replace('H_PH', history)).checks.find((c) => c.id === 'kanidm')!.bands;
    expect(bands.map((b) => b[2])).toEqual([1, 0, 1, 1]);
    expect(bands[2][1]).toBeLessThan(bands[3][0]);
  });

  const lane = (points: Lane['points']): Lane => ({ system: 'k8s', metric: '', unit: '', ref: '', color: 'azure', points, step: 300000, decimals: 0, marks: { crit: [], warn: [], note: [], maint: [] } });

  test('a series that stopped early has no value at window end', () => {
    expect(valueAtEnd(lane([[TO - 300000, 5]]), TO)).toBe(5);
    expect(valueAtEnd(lane([[TO - 3600000, 5]]), TO)).toBeNull();
  });

  test('a cursor inside a gap reads nothing instead of borrowing a neighbour', () => {
    const points = lane([[0, 1], [300000, null], [3600000, 2]]);
    expect(sampleAt(points, 0)).toBe(1);
    expect(sampleAt(points, 1800000)).toBeNull();
  });
});

describe('unknown never reads as healthy', () => {
  test('missing checks are unknown, a zero is a failure, and neither is passing', () => {
    const p = catalog.find((e) => e.refId === 'H_P')!;
    const frames = p.series.map((s) => frame('H_P', [s.labels.probe === 'jellyfin' ? 0 : 1], s.labels)).filter((_, i) => i !== 0);
    const checks = homeOf(replace('H_P', ...frames)).checks;
    expect(checks.filter((c) => c.status === 'unknown').length).toBe(1);
    expect(checks.find((c) => c.id === 'jellyfin')!.status).toBe('failed');
  });

  test('checks without an HTTP status are not failures, and every assertion survives', () => {
    expect(model.checks.filter((c) => c.http == null).map((c) => c.id)).toEqual(['k8s-gateway', 'gateway-services', 'gateway-admin', 'unifi-gateway']);
    expect(model.checks.every((c) => c.status === 'passing')).toBe(true);
    expect(model.checks.map((c) => c.assertion)).toContain('ICMP reachability');
    expect(new Set(model.checks.map((c) => c.group))).toEqual(new Set(['Services', 'Dependencies']));
  });

  test('a host that loses its up sample is not collecting, whatever else it reports', () => {
    const up = catalog.find((e) => e.refId === 'M_D')!;
    const frames = up.series.filter((s) => s.labels.instance !== 'spark-1').map((s) => frame('M_D', [1], s.labels));
    expect(homeOf(replace('M_D', ...frames)).hosts.find((h) => h.instance === 'spark-1')!.state).toBe('missing');
  });

  test('WAN findings land on Network, not on the Kubernetes catch-all', () => {
    const offline = homeOf(replace('H_WNA', frame('H_WNA', [0], { wan: 'T-Mobile (WAN1)' }), frame('H_WNA', [1], { wan: 'Starlink (WAN2)' })));
    const row = offline.review!.action.find((r) => r.id === 'wan/T-Mobile (WAN1)')!;
    expect(row.system).toBe('net');
    expect(statusOf(offline.review, 'net', true, false)).toEqual({ mark: 'warn', text: '1 need action' });
    expect(statusOf(offline.review, 'k8s', true, false).text).not.toContain('action');
  });

  test('an unknown path is unknown on the map, not offline and not fine', () => {
    const unknown = homeOf(without('H_WNG'));
    expect(unknown.wans.map((w) => w.status)).toEqual(['unknown', 'unknown']);
    expect(statusOf(unknown.review, 'net', true, false).text).toBe('1 unknown');
  });

  test('a query the model can\'t see still stops a quiet claim', () => {
    const quiet = homeOf(without('H_AL', 'H_AT'), { missing: 2, retained: 0, total: 40 });
    expect(quiet.review!.action.map((r) => r.id)).toContain('coverage/queries');
    expect(quiet.review!.confirmed).toBe(false);
    expect(briefingOf(quiet, false, false, '').state).toBe('Coverage incomplete');
  });

  test('retained data can show what it knew but never reads as a confirmed quiet', () => {
    const retained = homeOf(series, { missing: 0, retained: 3, total: 40 });
    expect(retained.review!.action.find((r) => r.id === 'coverage/queries')!.detail).toBe('3 retained of 40');
    expect(briefingOf(retained, false, false, '').tone).toBe('unknown');
  });

  test('a real action outranks a coverage gap and both stay visible', () => {
    const acting = homeOf(replace('H_P', frame('H_P', [0], { probe: 'jellyfin' })), { missing: 1, retained: 0, total: 40 });
    expect(acting.review!.action[0].level).toBe(3);
    expect(briefingOf(acting, false, false, '')).toMatchObject({ tone: 'bad', critical: 1 });
    expect(acting.review!.action.some((r) => r.unknown)).toBe(true);
  });

  test('with no review frames at all there is no review, rather than an empty one', () => {
    const empty = homeOf([emptyFrame('M_A') as unknown as FrameLike]);
    expect(empty.review).toBeNull();
    expect(statusOf(empty.review, 'overview', false, false).text).toBe('Unknown');
    expect(statusOf(empty.review, 'overview', false, true).text).toBe('Loading');
    expect(briefingOf(empty, false, false, '').state).toBe('Review unavailable');
  });

  test('monitoring evidence that\'s missing reads unknown, not delivered or failing', () => {
    const blind = homeOf(without('H_MH', 'H_MV', 'H_MA')).monitor;
    expect([blind.heartbeat, blind.rulesEvaluating]).toEqual([null, false]);
  });
});

describe('one model behind map, briefing and review', () => {
  test('map counts add up to the review, system by system', () => {
    const systems = new Set(review.recovered.map((r) => r.system));
    for (const system of systems) {
      const text = statusOf(review, system, true, false).text;
      const count = review.recovered.filter((r) => r.system === system).length;
      expect(text).toContain(`${count} resolved`);
    }
    expect(statusOf(review, 'k8s', true, false).text).toBe('2 notices · 4 resolved');
    expect(statusOf(review, 'nas', true, false)).toEqual({ mark: 'ring', text: '1 resolved' });
    expect(statusOf(review, 'overview', true, false).text).toBe('0 need action');
  });

  test('the same frames give the same answer through any prefix view', () => {
    expect(scoped(series, 'H_').series.every((f) => !f.refId?.startsWith('H_'))).toBe(true);
    expect(scoped(series, 'M_').series.map((f) => f.refId).sort()).toEqual(series.filter((f) => f.refId?.startsWith('M_')).map((f) => f.refId!.slice(2)).sort());
  });

  test('deriving never touches the frames it was given', () => {
    const before = JSON.stringify(series.slice(0, 5));
    buildHome(series, FROM, TO, noGaps);
    expect(JSON.stringify(series.slice(0, 5))).toBe(before);
  });
});

function frameStepBefore(ref: string) {
  return seriesOf({ series }, ref)[0].step;
}
