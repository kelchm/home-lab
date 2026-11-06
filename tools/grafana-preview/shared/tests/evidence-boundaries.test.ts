import { describe, expect, test } from 'vitest';
import { dur } from '../src/format';
import { buildHome } from '../src/model/home';
import { statusOf } from '../src/model/status';
import { buildNetwork } from '../src/model/network';
import { visibleSpans } from '../src/ui/LanePlot';
import { frame, FROM, TO } from './fixtures';

describe('small evidence boundaries', () => {
  test.each([[179.6, '3 h 0 min'], [59.6, '1 h 0 min'], [59.4, '59 min'], [120, '2 h 0 min'], [0, '1 min']])('duration %s min carries rounding across hours', (minutes, expected) => {
    expect(dur(Number(minutes) * 60000)).toBe(expected);
  });
  test('radios with channel or client evidence survive a missing busy query', () => {
    const network = buildNetwork([frame('I_net_radioChannel', [36], { ap_radio: 'ap-1' }), frame('I_net_radioClients', [2], { ap_radio: 'ap-2' })], []);
    expect(network.radios).toEqual([
      { radio: 'ap-1', channel: 36, busy: null, clients: null },
      { radio: 'ap-2', channel: null, busy: null, clients: 2 },
    ]);
  });
  test('marks outside the selected window cannot leave a minimum-width band', () => {
    expect(visibleSpans([[-10, -1], [-2, 0], [10, 20], [20, 30], [4, 4]], 0, 10)).toEqual([]);
    expect(visibleSpans([[-2, 4], [8, 12], [-2, 12]], 0, 10)).toEqual([[0, 4], [8, 10], [0, 10]]);
  });
});


describe('alert source attribution', () => {
  test.each([
    ['pve', { cluster: 'pve-sbx' }], ['pve', { platform: 'pve' }],
    ['nas', { job: 'synology-snmp' }], ['nas', { platform: 'synology' }],
    ['net', { job: 'unifi-api' }], ['net', { job: 'starlink-api' }],
    ['power', { job: 'pi-nut', platform: 'pi' }],
    ['power', { job: 'synology-snmp', alertname: 'UPSOnBattery' }],
    ['dev', { job: 'pi-chrony' }], ['dev', { platform: 'pi' }],
  ] as Array<[string, Record<string, string>]>)('maps explicit source labels to %s lane', (system, labels) => {
    const model = buildHome([frame('H_AH', [1, 1, 1], { alertname: 'TestAlert', severity: 'critical', ...labels })], FROM, TO, { missing: 0, retained: 0, total: 1 });
    const marked = model.lanes.filter((lane) => lane.marks.crit.length).map((lane) => lane.system);
    expect(marked).toEqual([system]);
  });
  test('does not guess a domain from an unknown exporter name', () => {
    const model = buildHome([frame('H_AH', [1], { job: 'unifi-unknown', severity: 'critical' })], FROM, TO, { missing: 0, retained: 0, total: 1 });
    expect(model.lanes.every((lane) => lane.marks.crit.length === 0)).toBe(true);
  });
  test('Overview marks unknown-only action coverage without counting it as a known action', () => {
    const review = { action: [{ id: 'coverage', label: 'Unknown', level: 2, kind: 'monitor', unknown: true, system: 'overview', spans: [] }], recovered: [], observe: [], maintenance: [], confirmed: false } as Parameters<typeof statusOf>[0];
    expect(statusOf(review, 'overview', true, false)).toEqual({ mark: 'warn', text: 'Coverage incomplete' });
  });
});
