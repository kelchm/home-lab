// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, test } from 'vitest';
import type { DataFrame, PanelData, TimeRange } from '@grafana/data';
const LoadingState = { Done: 'Done' as PanelData['state'], Error: 'Error' as PanelData['state'] };
const dateTime = (s: string) => ({ valueOf: () => Date.parse(s), toISOString: () => new Date(s).toISOString() });
const toDataFrame = (frame: unknown) => frame as DataFrame;
import { DomainView, type DomainViewProps } from '../src/domains/DomainView';
import { format, value } from '../src/domains/data';
import type { DomainDefinition, DomainPage } from '../src/domains/contract';
import { FOCUSES } from '../src/types';
import { HomeView } from '../src/HomeView';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
globalThis.ResizeObserver ??= class { observe() {} disconnect() {} unobserve() {} } as unknown as typeof ResizeObserver;
const roots: Root[] = [];
afterEach(() => { for (const root of roots.splice(0)) act(() => root.unmount()); document.body.innerHTML = ''; });
const range: TimeRange = { from: dateTime('2026-11-01T00:00Z'), to: dateTime('2026-11-01T01:00Z'), raw: { from: '2026-11-01T00:00:00.000Z', to: '2026-11-01T01:00:00.000Z' } } as TimeRange;
const ref = 'D_test_load';
const optional = 'D_test_temperature';
const frame = (id = ref, n = 42, entity = 'a') => toDataFrame({ refId: id, fields: [{ name: 'Time', values: [range.to.valueOf()], type: 'time' }, { name: 'Value', values: [n], type: 'number', labels: { host: entity } }] });
const domain: DomainDefinition = {
  id: 'spark', focus: 'Sparks', title: 'Sparks', accent: 'orchid',
  queries: [ref, optional].map((refId) => ({ refId, key: refId, source: 'synthetic', expr: refId, instant: true, range: false, scope: 'synthetic' })),
  pages: [{ id: 'hardware', title: 'Hardware', question: 'What was measured?', required: [ref], sections: (ctx) => [
    { kind: 'metrics', id: 'measurements', title: 'Measurements', items: [{ label: 'Load', value: format(value(ctx.series, ref, ctx.entity ? { host: ctx.entity } : {})) }, { label: 'Optional temperature', value: format(value(ctx.series, optional)) }] },
    { kind: 'table', id: 'identity', title: 'Identity', columns: ['Resource', 'State'], rows: ctx.entity ? [{ id: ctx.entity, cells: [{ text: ctx.entity, href: ctx.href({ entity: ctx.entity }) }, { text: 'Unknown', tone: 'unknown', detail: 'No readiness claim' }] }] : [], empty: 'No resource selected' },
    { kind: 'chart', id: 'load-history', title: 'Load history', refs: [ref], unit: 'percent', entityLabels: ['host'] },
  ] }],
};
const panel = (over: Partial<PanelData> = {}): PanelData => ({ state: LoadingState.Done, series: [frame(), frame(optional, 55)], timeRange: range, request: { targets: domain.queries, range } as unknown as PanelData['request'], ...over });
const failure = (ids = [ref, optional]) => panel({ state: LoadingState.Error, series: [], errors: ids.map((refId) => ({ refId, message: 'synthetic failure' })) });
const props = (over: Partial<DomainViewProps> = {}): DomainViewProps => ({ domain, page: domain.pages[0], data: panel(), timeRange: range, mode: 'recorded', timeZone: 'UTC', entity: null, href: (route) => `/local?entity=${route.entity ?? ''}`, deeper: (uid) => `/d/${uid}`, renderChart: ({ data }) => <div data-native-frames={data.series.length} />, ...over });
function mount(node: ReactNode) {
  const host = document.body.appendChild(document.createElement('div'));
  const root = createRoot(host); roots.push(root);
  const render = (next: ReactNode) => act(() => root.render(next));
  render(node);
  return { host, render, page: () => host.querySelector<HTMLElement>('.lab-domain')! };
}

describe('domain lifecycle', () => {
  test('resolves required evidence for the selected recording and keeps live requirements independent of recorded coverage', () => {
    const historicalRange = { ...range, from: dateTime('2026-10-31T23:00Z'), to: dateTime('2026-11-01T00:00Z'), raw: { from: '2026-10-31T23:00Z', to: '2026-11-01T00:00Z' } } as TimeRange;
    const scopedPage: DomainPage = { ...domain.pages[0], requiredFor: ({ mode, timeRange }) => mode === 'recorded' && timeRange.from.valueOf() < range.from.valueOf() ? [optional] : [ref] };
    const historicalFrame = frame(optional, 55);
    historicalFrame.fields[0].values = [historicalRange.to.valueOf()];
    const historicalData = panel({ series: [historicalFrame], timeRange: historicalRange, request: { targets: domain.queries, range: historicalRange } as unknown as PanelData['request'] });
    const base = { page: scopedPage, timeRange: historicalRange, data: historicalData };
    const { page, host, render } = mount(<DomainView {...props(base)} />);
    expect(page().dataset.freshness).toBe('fresh');
    expect(host.textContent).not.toContain('Required data is unavailable');
    render(<DomainView {...props({ ...base, mode: 'live' })} />);
    expect(page().dataset.freshness).toBe('unavailable');
    render(<DomainView {...props({ page: scopedPage, data: panel({ series: [frame(optional, 55)] }) })} />);
    expect(page().dataset.freshness).toBe('unavailable');
  });

  test('dates and dims retained values selected by the required-ref resolver', () => {
    const scopedPage: DomainPage = { ...domain.pages[0], requiredFor: () => [optional] };
    const { page, host, render } = mount(<DomainView {...props({ page: scopedPage })} />);
    render(<DomainView {...props({ page: scopedPage, data: failure() })} />);
    expect(page().dataset.freshness).toBe('stale');
    expect(host.querySelectorAll('dd')[0].textContent).toBe('Unknown');
    expect(host.querySelectorAll('dd')[1].textContent).toBe('55');
    expect(host.textContent).toContain('Retained values were received');
  });

  test('a failed first load says unknown and never manufactures zeroes', () => {
    const { page, host } = mount(<DomainView {...props({ data: failure() })} />);
    expect(page().dataset.freshness).toBe('failed');
    expect(host.textContent).toContain('The state of these resources is unknown');
    expect(host.querySelector('dd')?.textContent).toBe('Unknown');
  });
  test('a successful empty response stays distinct from a measured zero', () => {
    const { page, host, render } = mount(<DomainView {...props({ data: panel({ series: [] }) })} />);
    expect(page().dataset.freshness).toBe('unavailable');
    expect(host.querySelector('dd')?.textContent).toBe('Unknown');
    render(<DomainView {...props({ data: panel({ series: [frame(ref, 0)] }) })} />);
    expect(host.querySelector('dd')?.textContent).toBe('0');
    expect(page().dataset.freshness).toBe('fresh');
  });
  test('a failed refresh dates and dims retained values, then recovers', () => {
    const { host, render, page } = mount(<DomainView {...props()} />);
    render(<DomainView {...props({ data: failure() })} />);
    expect(page().dataset.freshness).toBe('stale');
    expect(host.querySelector('dd')?.textContent).toBe('42');
    expect(host.querySelector('.domain-retained')).not.toBeNull();
    expect(host.textContent).toContain('Retained values were received');
    expect(host.textContent).toContain('(UTC)');
    render(<DomainView {...props({ data: panel({ series: [frame(ref, 7)] }) })} />);
    expect(page().dataset.freshness).toBe('fresh');
    expect(host.querySelector('dd')?.textContent).toBe('7');
    expect(host.querySelector('.domain-retained')).toBeNull();
  });
  test('a datasource error with the previous nonempty frames dates and dims the old answer', () => {
    const initial = panel();
    const { host, render, page } = mount(<DomainView {...props({ data: initial })} />);
    render(<DomainView {...props({ data: { ...initial, state: LoadingState.Error, errors: [{ message: 'Datasource resolution failed' }] } })} />);
    expect(page().dataset.freshness).toBe('stale');
    expect(host.querySelector('dd')?.textContent).toBe('42');
    expect(host.querySelector('.domain-retained')).not.toBeNull();
    expect(host.textContent).toContain('Retained values were received');
  });
  test('an optional query failure does not poison page coverage or expose undated stale optional values', () => {
    const { host, render, page } = mount(<DomainView {...props()} />);
    render(<DomainView {...props({ data: panel({ state: LoadingState.Error, series: [frame()], errors: [{ refId: optional, message: 'synthetic optional failure' }] }) })} />);
    expect(page().dataset.freshness).toBe('fresh');
    expect(host.querySelectorAll('dd')[1].textContent).toBe('Unknown');
  });
  test.each(['source', 'range', 'entity', 'page'] as const)('clears retained values when %s changes', (change) => {
    const { host, render } = mount(<DomainView {...props({ entity: 'a' })} />);
    const override: Partial<DomainViewProps> = change === 'source' ? { mode: 'live' } : change === 'entity' ? { entity: 'b' } : change === 'page' ? { page: { ...domain.pages[0], id: 'detail' } } : { timeRange: { ...range, raw: { from: '2026-11-02T00:00Z', to: '2026-11-02T01:00Z' } } };
    render(<DomainView {...props({ data: failure(), entity: 'a', ...override })} />);
    expect(host.querySelector('dd')?.textContent).toBe('Unknown');
    expect(host.textContent).not.toContain('Retained values were received');
  });
  test('keeps an unrelated domain failure outside the page coverage', () => {
    const { page } = mount(<DomainView {...props({ data: panel({ state: LoadingState.Error, errors: [{ refId: 'D_net_devices', message: 'other domain' }] }) })} />);
    expect(page().dataset.freshness).toBe('fresh');
  });
});

describe('natural-height domain content', () => {
  test('a fixed chart resource reaches the native host without requiring a selected route entity', () => {
    const page: DomainPage = { ...domain.pages[0], sections: () => [{ kind: 'chart', id: 'fixed', title: 'Known probe', refs: [ref], unit: 'short', entityLabels: ['probe'], entity: 'athena-dsm' }] };
    const { host } = mount(<DomainView {...props({ page, renderChart: ({ entity }) => <div data-chart-entity={entity} /> })} />);
    expect(host.querySelector('[data-chart-entity]')?.getAttribute('data-chart-entity')).toBe('athena-dsm');
  });
  test('renders resource links, semantic tables, descriptions and chart data through the host seam', () => {
    const { host } = mount(<DomainView {...props({ entity: 'a' })} />);
    expect(host.querySelector('th')?.getAttribute('scope')).toBe('col');
    expect(host.querySelector('[role=region]')?.getAttribute('tabindex')).toBe('0');
    expect(host.querySelector('td a')?.getAttribute('href')).toBe('/local?entity=a');
    expect(host.textContent).toContain('No readiness claim');
    expect(host.querySelector('[data-native-frames]')?.getAttribute('data-native-frames')).toBe('2');
    expect(host.querySelector('[data-constrained]')).toBeNull();
    expect(host.textContent).toContain('Recorded observations');
    expect(host.textContent).toContain('Nov 1, 2026');
  });
  test('selects any of the eight internal destinations without external-comparison badges', () => {
    const links = Object.fromEntries(FOCUSES.map((focus) => [focus, `/a/lab?focus=${focus}`]));
    const { host } = mount(<HomeView data={panel()} timeRange={range} view="navigation" focus="Sparks" links={links} mode="recorded" />);
    expect(host.querySelectorAll('[data-system]')).toHaveLength(8);
    expect(host.querySelector('[aria-current=page]')?.getAttribute('data-system')).toBe('Sparks');
    expect(host.querySelectorAll('[data-outside]')).toHaveLength(0);
    expect(host.querySelectorAll('a[target]')).toHaveLength(0);
  });
});
