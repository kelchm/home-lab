import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { TimeRange } from '@grafana/data';
import type { DomainContext, DomainSection } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import { atEnd, availableFrames, collection, devicePorts, devices, paths, ports, queries, radios, reading, ref, registration } from './model';

interface Captured { labels: Record<string, string>; t: number[]; v: Array<number | null> }
interface CatalogQuery { refId: string; expr: string; series: Captured[] }
const root = new URL('../../../../', import.meta.url);
const catalog: CatalogQuery[] = JSON.parse(readFileSync(new URL('runtime/catalog.json', root), 'utf8'));
const deviceNames = (type: string) => catalog.find((item) => item.refId === 'I_net_deviceInfo')!.series.filter((item) => item.labels.type === type).map((item) => item.labels.name);
const [AP_A, AP_B, AP_C] = deviceNames('uap');
const SWITCH_A = deviceNames('usw')[1];
const SWITCH_B = deviceNames('usw')[3];
const { from: FROM, to: TO } = JSON.parse(readFileSync(new URL('fixtures/bounded.json', root), 'utf8')) as { from: number; to: number };
const frame = (key: string, values: Array<number | null>, labels: Record<string, string> = {}, times = values.map((_, i) => TO - (values.length - 1 - i) * 60000)): FrameLike => ({
  refId: ref(key), fields: [{ name: 'Time', type: 'time', values: times, config: { interval: 60000 } }, { name: 'Value', type: 'number', labels, values }],
});
const replay = (): FrameLike[] => queries.flatMap((query) => {
  const source = catalog.find((item) => item.refId === query.capture?.key);
  return (source?.series ?? []).map((series) => frame(query.refId.slice('D_net_'.length), series.v, series.labels, series.t));
});
const ctx = (series: FrameLike[] = replay(), options: Partial<DomainContext> = {}): DomainContext => ({
  series, mode: 'recorded', entity: null, timeZone: 'UTC',
  timeRange: { from: { valueOf: () => FROM }, to: { valueOf: () => TO }, raw: { from: FROM, to: TO } } as unknown as TimeRange,
  coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
  href: (route) => `http://preview.test/explore?${new URLSearchParams({ from: String(FROM), to: String(TO), 'var-source': 'replay', focus: 'Network', ...Object.fromEntries(Object.entries(route).filter(([, value]) => value != null).map(([key, value]) => [key, String(value)])) })}`,
  deeper: (uid, variables) => `http://preview.test/d/${uid}?${new URLSearchParams({ from: String(FROM), to: String(TO), 'var-source': 'replay', ...Object.fromEntries(Object.entries(variables ?? {}).map(([key, value]) => [`var-${key}`, value])) })}`,
  ...options,
});
const page = (id: string, context = ctx()) => domain.pages.find((page) => page.id === id)!.sections(context);
const table = (sections: DomainSection[], id: string) => {
  const section = sections.find((section) => section.id === id);
  if (section?.kind !== 'table') throw new Error(`Missing table ${id}`);
  return section;
};
const without = (key: string, label?: string) => replay().filter((item) => item.refId !== ref(key) || (label != null && !JSON.stringify(item.fields).includes(label)));
const live = (frames: FrameLike[]) => ctx(frames, { mode: 'live' });

describe('Network capture and semantics', () => {
  it('declares unique domain refs with real capture expressions and no embedded samples', () => {
    expect(domain.id).toBe('net');
    expect(domain.focus).toBe('Network');
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId).toMatch(/^D_net_/);
      expect(query.instant).not.toBe(query.range);
      expect(query).not.toHaveProperty('series');
      if (query.capture) {
        const original = catalog.find((item) => item.refId === query.capture?.key);
        expect(original, query.refId).toBeDefined();
        expect(query.expr).toBe(original!.expr);
      } else expect(query.optional).toBe(true);
    }
  });

  it('uses two independent labelled path observations, not a single network total', () => {
    const result = paths(ctx());
    expect(result.map((path) => path.state)).toEqual(['online', 'online']);
    expect(result.map((path) => path.group)).toEqual(['WAN2', 'WAN']);
    expect(result[0].latency).toBeCloseTo(0.02283333);
    expect(result[1].latency).toBeCloseTo(0.0455);
    expect(result[0].provisionedDown).toBe(350000000);
    expect(reading(ctx(), 'latency')).toBeNull();
  });

  it('does not treat monitor availability or interface identity as link state', () => {
    const result = paths(ctx(without('wanState')));
    expect(result.every((path) => path.availability === 100)).toBe(true);
    expect(result.every((path) => path.state === 'unknown')).toBe(true);
  });

  it('retains the missing path when one state series disappears', () => {
    const result = paths(ctx(without('wanState', 'Starlink (WAN2)')));
    expect(result).toHaveLength(2);
    expect(result.find((path) => path.name === 'Starlink (WAN2)')?.state).toBe('unknown');
    expect(result.find((path) => path.name === 'T-Mobile (WAN1)')?.state).toBe('online');
  });

  it('requires fresh metric age for positive or negative link claims', () => {
    const label = { wan: 'Starlink (WAN2)' };
    expect(paths(live([frame('wanState', [0], label), frame('wanAge', [22], label)]))[0].state).toBe('offline');
    expect(paths(live([frame('wanState', [0], label), frame('wanAge', [181], label)]))[0].state).toBe('unknown');
    expect(paths(live([frame('wanState', [1], label)]))[0].state).toBe('unknown');
    expect(paths(live([frame('wanState', [2], label), frame('wanAge', [22], label)]))[0].state).toBe('unknown');
  });

  it('separates a failed collector from an offline path', () => {
    const context = live([...replay(), frame('unifiCollector', [0])]);
    expect(collection(context, 'unifi')).toBe('failed');
    expect(paths(context).every((path) => path.state === 'unknown')).toBe(true);
    expect(collection(ctx(), 'unifi')).toBe('unknown');
    expect(paths(ctx()).every((path) => path.state === 'online')).toBe(true);
  });

  it('does not allow duplicate labelled series to resolve arbitrarily', () => {
    const context = ctx([...replay(), frame('deviceCpu', [99], { name: SWITCH_A })]);
    expect(devices(context).find((device) => device.name === SWITCH_A)?.cpu).toBeNull();
    expect(devices(context).find((device) => device.name === SWITCH_B)?.cpu).toBeCloseTo(56.1);
  });

  it('retains a device through independent metric labels when its info series disappears', () => {
    const context = ctx(without('deviceInfo', AP_B));
    const device = devices(context).find((device) => device.name === AP_B)!;
    expect(device.identityObserved).toBe(false);
    expect(device.metricsObserved).toBe(true);
    expect(device.type).toBe('radio');
    expect(device.cpu).toBeCloseTo(5.8);
    expect(device.version).toBeNull();
    const row = table(page('devices', context), 'network-devices').rows.find((row) => row.id === device.name)!;
    expect(row.cells).toContainEqual({ text: 'Metrics only', tone: 'neutral' });
    expect(JSON.stringify(row)).not.toMatch(/offline|healthy/i);
    expect(table(page('device', { ...context, entity: AP_B }), 'network-radios').rows).toHaveLength(2);
  });

  it('treats a completely absent selected device as unavailable without inventing inventory', () => {
    const removed = replay().filter((item) => !JSON.stringify(item.fields).includes(AP_B));
    expect(devices(ctx(removed)).find((device) => device.name === AP_B)).toBeUndefined();
    const sections = page('device', ctx(removed, { entity: AP_B }));
    expect(sections.find((section) => section.id === 'entity-unavailable')).toMatchObject({ kind: 'text', tone: 'unknown' });
    expect(sections.some((section) => section.kind === 'chart')).toBe(false);
  });

  it('keeps other observations useful when one entire device query fails', () => {
    const context = ctx(without('deviceCpu'));
    expect(devices(context)).toHaveLength(10);
    expect(devices(context).every((device) => device.cpu === null)).toBe(true);
    expect(devices(context).find((device) => device.name === AP_A)?.clients).toBe(34);
    expect(domain.pages.find((page) => page.id === 'devices')!.required).toContain(ref('deviceCpu'));
  });

  it('does not replace an unsupported AP temperature with zero', () => {
    expect(devices(ctx()).find((device) => device.name === AP_B)?.temperature).toBeNull();
    expect(devices(ctx()).find((device) => device.name === SWITCH_A)?.temperature).toBe(52);
  });

  it('adds newly observed devices without claiming baseline completeness', () => {
    const found = devices(ctx([...replay(), frame('deviceCpu', [10], { name: 'New switch' })])).find((device) => device.name === 'New switch')!;
    expect(found.cpu).toBe(10);
    expect(found.identityObserved).toBe(false);
    expect(found.type).toBe('Unknown');
    expect(found.version).toBeNull();
  });

  it('preserves AP and band labels for all recorded radios', () => {
    const result = radios(ctx());
    expect(result).toHaveLength(9);
    expect(result.find((radio) => radio.id === `${AP_A} · 6 GHz`)).toMatchObject({ name: AP_A, band: '6', channel: 5, busy: 10, clients: 1 });
    expect(result.find((radio) => radio.id === `${AP_B} · 5 GHz`)).toMatchObject({ channel: 132, busy: 1, clients: 1 });
  });

  it('keeps channels and clients when the radio-busy query is missing', () => {
    const result = radios(ctx(without('radioBusy')));
    expect(result).toHaveLength(9);
    expect(result.every((radio) => radio.busy === null)).toBe(true);
    expect(result.find((radio) => radio.id === `${AP_C} · 5 GHz`)?.clients).toBe(3);
  });

  it('handles raw radio labels, composite AP names, missing band and ambiguous duplicates', () => {
    const context = live([
      frame('radioChannel', [36], { ap_radio: 'Office · East AP · 5 GHz' }),
      frame('radioBusy', [40], { name: 'Office · East AP', band: '5' }),
      frame('radioClients', [2], { name: 'Office · East AP', radio: 'na' }),
      frame('radioBusy', [9], { ap_radio: 'Malformed label' }),
    ]);
    expect(radios(context).find((radio) => radio.name === 'Office · East AP')).toMatchObject({ band: '5', channel: 36, busy: 40, clients: 2 });
    expect(radios(context).find((radio) => radio.id === 'Malformed label')).toMatchObject({ name: null, band: null, busy: 9 });
    const duplicate = live([...context.series, frame('radioBusy', [2], { name: 'Office · East AP', band: '5', source: 'other-controller' })]);
    expect(radios(duplicate).find((radio) => radio.name === 'Office · East AP')?.busy).toBeNull();
  });

  it('preserves gateway port identity without assigning a provider from row order', () => {
    expect(ports(ctx()).map((port) => port.id)).toEqual(['eth8', 'eth9']);
    const section = table(page('paths'), 'gateway-ports');
    expect(section.rows[0].cells[0]).toMatchObject({ text: 'eth8' });
    expect(section.rows.every((row) => JSON.stringify(row.cells).includes('Unconfirmed'))).toBe(true);
    expect(ports(ctx(without('wanTxHistory'))).every((port) => port.tx === null)).toBe(true);
    expect(ports(ctx(without('wanTxHistory')))[0].rx).toBeCloseTo(800443.4916667);
  });

  it('scopes native port identifiers and retains port names without mixing devices', () => {
    const context = live([
      frame('portRxHistory', [1024], { name: SWITCH_A, port_id: '1', port_num: '1', port_name: 'Lab uplink' }),
      frame('portTxHistory', [2048], { name: SWITCH_A, port_id: '1', port_num: '1', port_name: 'Lab uplink' }),
      frame('portRxHistory', [99], { name: SWITCH_B, port_id: '1', port_name: 'Other port' }),
      frame('portRxHistory', [0], { name: SWITCH_A, port_num: '2', port_name: 'Unused?' }),
    ]);
    expect(devicePorts(context, SWITCH_A)).toEqual([{ id: '1', name: 'Lab uplink', rx: 1024, tx: 2048 }, { id: '2', name: 'Unused?', rx: 0, tx: null }]);
    expect(devicePorts(context, SWITCH_B)).toEqual([{ id: '1', name: 'Other port', rx: 99, tx: null }]);
  });

  it('does not infer a negative registration from an or-vector-zero capture', () => {
    expect(registration(ctx())).toBe('Registered observation');
    expect(registration(ctx([frame('tmobileRegistered', [0])]))).toMatch(/^Unknown/);
    expect(registration(ctx([]))).toMatch(/^Unknown/);
    expect(registration(live([frame('tmobileRegistered', [1]), frame('tmobileCollector', [0])]))).toMatch(/^Unknown/);
  });

  it('does not backfill trailing gaps, nonfinite values or another time window', () => {
    const series = { labels: {}, points: [[TO - 60000, 12], [TO, null]] as Array<[number, number | null]>, step: 60000 };
    expect(atEnd(series, ctx())).toBeNull();
    expect(atEnd({ ...series, points: [[TO - 180000, 12]] }, ctx())).toBeNull();
    expect(atEnd({ ...series, points: [[TO + 1, 12]] }, ctx())).toBeNull();
    expect(atEnd({ ...series, points: [[TO, Number.NaN]] }, ctx())).toBeNull();
    expect(reading(ctx([frame('deviceCpu', [0], { name: SWITCH_A })]), 'deviceCpu', { name: SWITCH_A })).toBe(0);
  });

  it('rejects uncaptured device histories in recorded mode even if frames are accidentally supplied', () => {
    const supplied = frame('portRxHistory', [500], { name: SWITCH_A, port_id: '1' });
    expect(availableFrames(ctx([supplied]))).toEqual([]);
    expect(devicePorts(ctx([supplied]), SWITCH_A)).toEqual([]);
    expect(devicePorts(live([supplied]), SWITCH_A)[0].rx).toBe(500);
  });
});

describe('Network pages and resource journeys', () => {
  it('composes every page with honest empty and invalid-selection states', () => {
    for (const definition of domain.pages) for (const entity of [null, 'missing / + ? &']) {
      const sections = definition.sections(ctx([], { entity }));
      expect(sections.length).toBeGreaterThan(0);
      expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
      for (const section of sections) if (section.kind === 'table') {
        expect(section.empty).toBeTruthy();
        for (const row of section.rows) expect(row.cells).toHaveLength(section.columns.length);
      }
      expect(JSON.stringify(sections)).not.toMatch(/No action needed|healthy|NaN|Infinity/);
    }
  });

  it('uses selected WAN/port/device labels for native charts and never substitutes all resources', () => {
    const pathCharts = page('path', ctx(undefined, { entity: 'Starlink (WAN2)' })).filter((section) => section.kind === 'chart');
    expect(pathCharts.find((section) => section.id === 'path-latency')).toMatchObject({ entityLabels: ['wan'], refs: [ref('latencyHistory')], unit: 's' });
    const deviceCharts = page('device', ctx(undefined, { entity: AP_A })).filter((section) => section.kind === 'chart');
    expect(deviceCharts.every((section) => section.entityLabels?.includes('name'))).toBe(true);
    expect(page('port', ctx(undefined, { entity: 'eth8' })).filter((section) => section.kind === 'chart').every((section) => section.entityLabels?.includes('port'))).toBe(true);
    for (const view of ['path', 'device', 'port']) expect(page(view, ctx(undefined, { entity: 'Not present' })).some((section) => section.kind === 'chart')).toBe(false);
  });

  it('keeps selected AP radios separate from other APs and presents missing history honestly', () => {
    const sections = page('device', ctx(undefined, { entity: AP_A }));
    expect(table(sections, 'network-radios').rows).toHaveLength(3);
    expect(table(sections, 'network-radios').rows.every((row) => row.id.startsWith(`${AP_A} · `))).toBe(true);
    expect(JSON.stringify(sections)).toContain('not captured');
    expect(JSON.stringify(sections)).not.toContain(`${AP_B} ·`);
  });

  it('separates Starlink throughput units and 5G diagnostics from aggregate gateway traffic', () => {
    const starlink = page('path', ctx(undefined, { entity: 'Starlink (WAN2)' }));
    expect(starlink.find((section) => section.id === 'dish-rx')).toMatchObject({ kind: 'chart', unit: 'bps', refs: [ref('dishDownHistory')] });
    expect(starlink.some((section) => section.kind === 'chart' && section.refs.includes(ref('wanRxHistory')))).toBe(false);
    const cellular = page('path', ctx(undefined, { entity: 'T-Mobile (WAN1)' }));
    expect(cellular.find((section) => section.id === 'cellular-rsrp')).toMatchObject({ kind: 'chart', unit: 'dBm' });
    expect(cellular.some((section) => section.id === 'dish-rx')).toBe(false);
    expect(JSON.stringify(starlink)).toContain('does not prove successful failover');
  });

  it('preserves source, window and entity through internal path, device, radio and port links', () => {
    const sections = [...page('paths'), ...page('devices')];
    const links = sections.flatMap((section) => section.kind === 'table' ? section.rows.flatMap((row) => row.cells.flatMap((cell) => typeof cell !== 'string' && cell.href ? [cell.href] : [])) : []);
    expect(links.length).toBeGreaterThan(10);
    for (const href of links) {
      const url = new URL(href);
      expect(url.searchParams.get('from')).toBe(String(FROM));
      expect(url.searchParams.get('to')).toBe(String(TO));
      expect(url.searchParams.get('var-source')).toBe('replay');
      expect(url.searchParams.get('focus')).toBe('Network');
      expect(url.searchParams.get('entity')).toBeTruthy();
      expect(domain.pages.some((item) => item.id === url.searchParams.get('view'))).toBe(true);
    }
  });

  it('uses ctx.deeper with native variable names, literal selector escaping and the same window', () => {
    const sections = page('device', ctx([...replay(), frame('deviceInfo', [1], { name: 'Office [AP].2', type: 'uap', version: 'test' })], { entity: 'Office [AP].2' }));
    const tools = sections.find((section) => section.id === 'network-tools');
    if (tools?.kind !== 'links') throw new Error('Missing native links');
    const link = new URL(tools.items[0].href);
    expect(link.pathname).toBe('/d/g5wFWqxZk');
    expect(link.searchParams.get('var-AP')).toBe('Office \\[AP\\]\\.2');
    expect(link.searchParams.get('var-source')).toBe('replay');
    expect(link.searchParams.get('from')).toBe(String(FROM));
    expect(link.searchParams.get('to')).toBe(String(TO));
  });
});
