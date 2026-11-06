// @vitest-environment jsdom
import { readdirSync, readFileSync } from 'node:fs';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { PanelData } from '@grafana/data';
import { HomeView, modeOfDatasource, VIEWS } from '../src/HomeView';
import { rememberQueries } from '../src/lifecycle';
import type { HomeViewProps, View } from '../src/types';
import { useHighlight } from '../src/ui/highlight';
import { overflowOf } from '../src/ui/parts';
import { catalog, emptyFrame, frame, FROM, panelData, range, sharedRequest, TO } from './fixtures';

// React's act() needs this flag, and jsdom has no ResizeObserver. Layout-driven behaviour is covered by overflowOf and by the hosts' browser runs.
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const mountedRoots = new Set<Root>();
afterEach(() => {
  act(() => { for (const root of mountedRoots) root.unmount(); });
  mountedRoots.clear();
  document.body.innerHTML = '';
});

const links = { Overview: '/d/x?from=1&to=2&var-focus=Overview', Network: '/d/x?from=1&to=2&var-focus=Network' };
const props = (view: View, over: Partial<HomeViewProps> = {}): HomeViewProps => ({ data: panelData(), timeRange: range(), view, focus: 'Overview', links, constrained: true, mode: 'recorded', ...over });

function mount(node: ReactNode) {
  const host = document.body.appendChild(document.createElement('div'));
  const root: Root = createRoot(host);
  mountedRoots.add(root);
  const render = (next: ReactNode) => act(() => root.render(next));
  render(node);
  return { host, render, unmount: () => { act(() => root.unmount()); mountedRoots.delete(root); } };
}
const show = (view: View, over: Partial<HomeViewProps> = {}) => mount(<HomeView {...props(view, over)} />).host;
const root = (host: Element) => host.querySelector('.hlc') as HTMLElement;
const failing = (refs: RegExp, base = panelData()) =>
  ({ ...base, state: 'Error', series: base.series.map((f) => (refs.test(f.refId ?? '') ? emptyFrame(f.refId!) : f)), errors: [...new Set(base.series.map((f) => f.refId!).filter((r) => refs.test(r)))].map((refId) => ({ refId, message: 'boom' })) }) as PanelData;

describe('the hooks hosts and evidence rely on', () => {
  test.each(VIEWS)('%s exposes its view, freshness and source on the root', (view) => {
    expect(root(show(view)).dataset).toMatchObject({ view, freshness: 'fresh', mode: 'recorded' });
  });

  test('all eight systems are on the map under their display names', () => {
    expect([...show('navigation').querySelectorAll<HTMLElement>('[data-system]')].map((el) => el.dataset.system)).toEqual(['Overview', 'Kubernetes', 'Proxmox', 'Sparks', 'Athena', 'Network', 'Power', 'Devices']);
  });

  test('every machine has a row, and nothing is hard-coded to 13', () => {
    expect(show('machines').querySelectorAll('[data-host]').length).toBe(13);
    const extra = catalog.filter((e) => e.refId.startsWith('M_')).flatMap((e) => e.series.slice(0, 12).map((s) => frame(e.refId, s.v.filter((v) => v != null) as number[], { ...s.labels, instance: `${s.labels.instance}-stress` }, s.t)));
    const base = panelData();
    expect(show('machines', { data: { ...base, series: [...base.series, ...extra] } }).querySelectorAll('[data-host]').length).toBe(25);
  });
});

describe('navigation stays honest about what exists here', () => {
  let nav: HTMLElement;
  beforeEach(() => { nav = show('navigation', { focus: 'Network' }); });
  const item = (name: string) => nav.querySelector(`[data-system="${name}"]`) as HTMLAnchorElement;

  test('Overview and Network use the host\'s links and open in place', () => {
    expect(item('Network').getAttribute('href')).toBe(links.Network);
    expect(item('Overview').getAttribute('href')).toBe(links.Overview);
    expect(item('Network').hasAttribute('target')).toBe(false);
  });

  test('the current focus is marked, and anything unknown reads as Overview', () => {
    expect(item('Network').getAttribute('aria-current')).toBe('page');
    expect(show('navigation', { focus: '$focus' }).querySelector('[aria-current]')?.getAttribute('data-system')).toBe('Overview');
  });

  test('the other six link out in a new tab, at this window, and say they are outside the comparison', () => {
    const outside = [...nav.querySelectorAll<HTMLAnchorElement>('[data-outside]')];
    expect(outside.length).toBe(6);
    for (const link of outside) {
      expect(link.getAttribute('target')).toBe('_blank');
      expect(link.getAttribute('href')).toContain(`from=${FROM}&to=${TO}`);
      expect(link.getAttribute('aria-label')).toContain('Outside this comparison');
    }
    expect(show('briefing').textContent).toContain('Outside this comparison');
  });

  test('a host can point a system somewhere of its own, and then it opens in place', () => {
    const link = show('navigation', { links: { ...links, Kubernetes: '/d/local' } }).querySelector('[data-system="Kubernetes"]')!;
    expect(link.getAttribute('href')).toBe('/d/local');
    expect(link.hasAttribute('target')).toBe(false);
  });

  test('without a host link a built system is plain text, never a dead link', () => {
    expect(show('navigation', { links: {} }).querySelector('[data-system="Network"]')?.tagName).toBe('SPAN');
  });
});

describe('recorded and live are never confused', () => {
  const now = range('now-24h', 'now', [Date.now() - 86400000, Date.now()]);
  const live = (over: Partial<HomeViewProps> = {}) => ({ mode: 'live' as const, timeRange: now, data: panelData({ timeRange: now }), ...over });

  test.each(['briefing', 'activity', 'review', 'machines', 'checks', 'network-summary', 'network-inventory'] as View[])('%s says a replay is recorded', (view) => {
    expect(show(view).textContent).toMatch(/Record/i);
  });

  test('a replay never reads as current', () => {
    expect(show('briefing').querySelector('[data-state]')?.getAttribute('data-state')).toBe('No action needed at window end');
    const text = show('network-summary').textContent;
    expect(text).toContain('Online at window end');
    expect(text).not.toContain('Reporting online');
  });

  test('live and current drops the qualifier and the tag', () => {
    const host = show('briefing', live());
    expect(host.querySelector('[data-state]')?.getAttribute('data-state')).toBe('No action needed');
    expect(host.textContent).toContain('Live');
    expect(show('review', live()).querySelector('.eye')?.textContent).not.toMatch(/Record|Past/i);
  });

  test('a live source over a window that has ended is still historical', () => {
    const host = show('briefing', { mode: 'live' });
    expect(host.textContent).toContain('Live source · past window');
    expect(host.querySelector('[data-state]')?.getAttribute('data-state')).toContain('at window end');
  });

  test('the source is detected from the request, and a consumer that can\'t see it says so', () => {
    expect(root(show('briefing', { mode: undefined })).dataset.mode).toBe('recorded');
    const shared = { ...panelData(), request: sharedRequest };
    const host = show('briefing', { mode: undefined, data: shared });
    expect(root(host).dataset.mode).toBe('unknown');
    expect(host.textContent).toContain('Source unknown');
    expect([modeOfDatasource('comparison'), modeOfDatasource('comparison-live'), modeOfDatasource('other')]).toEqual(['recorded', 'live', undefined]);
  });
});

describe('failure, through the real view', () => {
  test('a failed refresh goes stale with its time, then recovery clears it', () => {
    const view = mount(<HomeView {...props('review')} />);
    view.render(<HomeView {...props('review', { data: failing(/^H_/) })} />);
    expect(root(view.host).dataset.freshness).toBe('stale');
    expect(view.host.textContent).toMatch(/Query failed · showing data received \d\d:\d\d:\d\d/);
    expect(view.host.querySelector('[data-group="resolved"]')).not.toBeNull();
    view.render(<HomeView {...props('review')} />);
    expect(root(view.host).dataset.freshness).toBe('fresh');
    expect(view.host.textContent).not.toContain('Query failed');
  });

  test('stale data never claims a quiet review, however clean it looks', () => {
    const view = mount(<HomeView {...props('briefing')} />);
    view.render(<HomeView {...props('briefing', { data: failing(/^H_/) })} />);
    expect(view.host.querySelector('[data-state]')?.getAttribute('data-state')).toBe('Coverage incomplete');
  });

  test('moving the range during an outage drops the retained copy instead of relabelling it', () => {
    const view = mount(<HomeView {...props('machines')} />);
    const moved = range('now-6h', 'now');
    view.render(<HomeView {...props('machines', { data: { ...failing(/^M_/), timeRange: moved }, timeRange: moved })} />);
    expect(root(view.host).dataset.freshness).toBe('partial');
    expect(view.host.querySelectorAll('[data-host]').length).toBe(0);
  });

  test('switching source does the same: recorded frames never survive under a live label', () => {
    const view = mount(<HomeView {...props('machines')} />);
    const noRequest = (data: PanelData) => ({ ...data, request: undefined });
    view.render(<HomeView {...props('machines', { mode: 'live', data: noRequest(failing(/^M_/)) })} />);
    expect(view.host.querySelectorAll('[data-host]').length).toBe(0);
  });

  test('an edited source query is noticed by a consumer of that source', () => {
    const request = { ...sharedRequest!, dashboardUID: 'test-dashboard', panelId: 2 };
    const sourceRequest = { ...panelData().request!, dashboardUID: 'test-dashboard', panelId: 1 };
    const base = { ...panelData(), request };
    rememberQueries(sourceRequest);
    const view = mount(<HomeView {...props('machines', { data: base })} />);
    const edited = sourceRequest;
    rememberQueries({ ...edited, targets: edited.targets.map((t) => (t.refId === 'M_A' ? { ...t, expr: 'edited' } : t)) } as PanelData['request']);
    view.render(<HomeView {...props('machines', { data: { ...failing(/^M_A$/, base), request } })} />);
    expect(root(view.host).dataset.freshness).toBe('partial');
    rememberQueries(sourceRequest);
  });

  test('a first-load failure shows nothing current and invents no zeros', () => {
    const host = show('activity', { data: failing(/./) });
    expect(root(host).dataset.freshness).toBe('failed');
    expect(host.querySelectorAll('[data-state="failed"]').length).toBe(7);
    expect(host.textContent).toContain('Query failed');
    expect(host.querySelector('.sumrow em')?.textContent).toBe('– / 10');
    expect(host.querySelector('.sumrow em')?.className).toBe('unknown');
  });

  test('one failed lane stays one failed lane', () => {
    const host = show('activity', { data: failing(/^R_spark_tokens$/) });
    expect(root(host).dataset.freshness).toBe('partial');
    expect(host.querySelector('[data-lane="Sparks"]')?.getAttribute('data-state')).toBe('failed');
    expect(host.querySelector('[data-lane="Kubernetes"]')?.getAttribute('data-state')).toBe('current');
  });

  test('a settled run with no frames says it can\'t tell, and names no cause', () => {
    const host = show('machines', { data: { ...panelData(), series: [] } });
    expect(root(host).dataset.freshness).toBe('unavailable');
    expect(host.textContent).toContain('can’t tell empty from failed');
    expect(host.textContent).not.toMatch(/connection|no response from grafana|network error/i);
  });

  test('loading says loading rather than unknown', () => {
    const host = show('navigation', { data: { ...panelData(), state: 'Loading', series: [] } as PanelData });
    expect(root(host).dataset.freshness).toBe('loading');
    expect(host.querySelector('[data-system="Overview"] small')?.textContent).toBe('Loading');
    expect(host.textContent).not.toContain('Unknown');
  });

  test('a failure elsewhere leaves Machines alone while the map stops claiming quiet', () => {
    const data = failing(/^H_P$/);
    expect(root(show('machines', { data })).dataset.freshness).toBe('fresh');
    expect(root(show('navigation', { data })).dataset.freshness).not.toBe('fresh');
    expect(show('navigation', { data }).textContent).not.toContain('Nothing flagged');
  });

  test('a failed query nothing here reads costs no coverage, because the error names it precisely', () => {
    const data = failing(/^R_k8s_cores$/);
    const host = show('briefing', { data });
    expect(root(host).dataset.freshness).toBe('fresh');
    expect(host.querySelector('[data-state]')?.getAttribute('data-state')).toBe('No action needed at window end');
    expect(root(show('machines', { data: failing(/^I_net_wanMap$/) })).dataset.freshness).toBe('fresh');
  });

  test('a panel in Grafana\'s error state marks itself so the corner badge clears the heading', () => {
    expect(root(show('machines', { data: failing(/^H_P$/) })).dataset.errored).toBe('true');
    expect(root(show('machines')).dataset.errored).toBeUndefined();
  });
});

describe('constrained panels and natural pages', () => {
  test('constrained fills the panel; natural takes its own height', () => {
    expect(root(show('machines')).dataset.constrained).toBe('true');
    expect(root(show('machines', { constrained: false })).dataset.constrained).toBeUndefined();
    expect(show('machines', { constrained: false }).querySelector('.fit')).toBeNull();
  });

  test('the check roll-up is pinned outside the scroll in a panel and gives way to the checks view on a page', () => {
    const panel = show('activity');
    expect(panel.querySelector('.fit .sumrow')).toBeNull();
    expect(panel.querySelector('.sumrow em')?.textContent).toBe('10 / 10');
    expect(show('activity', { constrained: false }).querySelector('.sumrow')).toBeNull();
  });

  test('service checks are an open list in a panel and their own disclosure on a page', () => {
    expect(show('checks').querySelector('details')).toBeNull();
    expect(show('checks').querySelectorAll('[data-check]').length).toBe(10);
    const page = show('checks', { constrained: false });
    expect(page.querySelector('details')?.hasAttribute('open')).toBe(false);
    expect(page.querySelectorAll('details [data-check]').length).toBe(10);
  });

  test('every check keeps its assertion, and only HTTP checks show a status code', () => {
    const rows = show('checks').querySelectorAll<HTMLElement>('[data-check]');
    const text = (id: string) => [...rows].find((row) => row.dataset.check === id)?.textContent;
    expect(text('homepage')).toContain('HTTP 401');
    expect(text('unifi-gateway')).toContain('ICMP reachability');
    expect(text('unifi-gateway')).not.toContain('HTTP');
    expect(text('gateway-services')).toContain('TCP / 443');
  });

  test('no surface renders the three named retired headline cards', () => {
    const text = VIEWS.map((view) => show(view).textContent).join(' ');
    expect(text).not.toMatch(/Tokens written|Backup freshness|Controller availability/);
  });

  test('the overflow cue counts what is actually hidden', () => {
    const rowBottoms = [100, 200, 300, 400];
    expect(overflowOf({ scrollTop: 0, clientHeight: 250, scrollHeight: 400, rowBottoms })).toEqual({ above: false, below: true, rows: 2 });
    expect(overflowOf({ scrollTop: 150, clientHeight: 250, scrollHeight: 400, rowBottoms })).toEqual({ above: true, below: false, rows: 0 });
    expect(overflowOf({ scrollTop: 0, clientHeight: 400, scrollHeight: 400, rowBottoms })).toEqual({ above: false, below: false, rows: 0 });
  });
});

describe('what ships', () => {
  test('no recorded value exists in anything the bundle is built from', () => {
    const source = readdirSync('src', { recursive: true, encoding: 'utf8' })
      .filter((file) => /\.(ts|tsx|js)$/.test(file))
      .map((file) => readFileSync(`src/${file}`, 'utf8'))
      .join('\n');
    const recorded = catalog.flatMap((e) => e.series.flatMap((s) => [s.labels.name, s.labels.source, s.labels.ap_radio])).filter(Boolean);
    expect(recorded.length).toBeGreaterThan(10);
    expect([...new Set(recorded)].filter((value) => source.includes(value))).toEqual([]);
    expect(source).not.toContain(String(TO));
  });
});


describe('reviewed evidence boundaries', () => {
  test.each([0, 2])('registration value %s is unknown, not negative evidence', (value) => {
    const base = panelData();
    const data = { ...base, series: [...base.series.filter((item) => item.refId !== 'I_net_tmobileRegistered'), frame('I_net_tmobileRegistered', [value])] };
    const host = show('network-summary', { data });
    expect(host.textContent).toContain('registration unknown');
    expect(host.textContent).not.toContain('not registered');
  });

  test('natural-height checks keep their selected window and recorded source', () => {
    const host = show('checks', { constrained: false });
    expect(host.querySelector('.eye')?.textContent).toMatch(/Reachability.*24 h.*Recorded/);
  });

  test.each(['missing', 'failed', 'loading'])('unavailable inventory counts stay unknown when %s', (state) => {
    const base = panelData();
    const inventory = /^I_net_(deviceInfo|radioChannel|radioBusy|radioClients)$/;
    const data = state === 'failed' ? failing(inventory, base) : { ...base, state: state === 'loading' ? 'Loading' : 'Done', series: base.series.filter((item) => !inventory.test(item.refId ?? '')) } as PanelData;
    const host = show('network-inventory', { data });
    expect(host.textContent).toContain('Device count unknown');
    expect(host.textContent).toContain('Radio count unknown');
    expect(host.textContent).not.toMatch(/0 devices|0 radios/);
  });

  test('successful empty inventories can report zero', () => {
    const base = panelData();
    const inventory = /^I_net_(deviceInfo|radioChannel|radioBusy|radioClients)$/;
    const host = show('network-inventory', { data: { ...base, series: base.series.map((item) => inventory.test(item.refId ?? '') ? emptyFrame(item.refId!) : item) } });
    expect(host.textContent).toContain('0 devices');
    expect(host.textContent).toContain('0 radios');
  });

  test.each(['loading', 'failed'])('retained inventory counts keep their own receipt time while %s', (state) => {
    const at = Date.UTC(2026, 10, 6, 12, 0, 0);
    const now = vi.spyOn(Date, 'now').mockReturnValue(at);
    try {
      const initial = panelData();
      const view = mount(<HomeView {...props('network-inventory', { data: initial })} />);
      const counts = [...view.host.querySelectorAll('.pill')].map((pill) => pill.textContent);
      now.mockReturnValue(at + 60_000);
      view.render(<HomeView {...props('network-inventory', { data: failing(/^I_net_deviceInfo$/, initial) })} />);
      now.mockReturnValue(at + 120_000);
      const next = state === 'loading' ? { ...initial, state: 'Loading', series: [] } as PanelData : failing(/^I_net_(deviceInfo|radioChannel|radioBusy|radioClients)$/, initial);
      view.render(<HomeView {...props('network-inventory', { data: next })} />);
      const pills = [...view.host.querySelectorAll('.pill')];
      expect(pills.map((pill) => pill.textContent)).toEqual(counts.map((text) => `${text} · ${state === 'loading' ? 'updating' : 'retained'}`));
      expect(pills[0].querySelector('[title]')?.getAttribute('title')).toBe('Inventory received Nov 6 07:00:00');
      expect(pills[1].querySelector('[title]')?.getAttribute('title')).toBe('Inventory received Nov 6 07:01:00');
      expect(view.host.querySelectorAll('[data-device]').length).toBeGreaterThan(0);
    } finally { now.mockRestore(); }
  });

  test.each([true, false])('refreshes label retained data with its receipt time (old rows present: %s)', (rows) => {
    const initial = panelData();
    const view = mount(<HomeView {...props('machines', { data: initial })} />);
    view.render(<HomeView {...props('machines', { data: { ...initial, state: 'Loading', series: rows ? initial.series : [] } as PanelData })} />);
    expect(root(view.host).dataset.freshness).toBe('loading');
    expect(view.host.textContent).toMatch(/Updating · showing data received \d\d:\d\d:\d\d/);
    expect(view.host.querySelectorAll('[data-host]')).toHaveLength(13);
    expect(view.host.textContent).not.toContain('Query failed');
  });

  test('a single observed CPU history sample renders a visible mark', () => {
    const base = panelData();
    const history = base.series.find((item) => item.refId === 'M_E')!;
    const single = { ...history, length: 1, fields: history.fields.map((field) => ({ ...field, values: field.values.slice(-1) })) };
    const host = show('machines', { data: { ...base, series: [...base.series.filter((item) => item.refId !== 'M_E'), single] } });
    expect(host.querySelectorAll('.msp circle')).toHaveLength(1);
  });
});


describe('small view state boundaries', () => {
  test.each(['constructor', 'toString', '__proto__', 'not-a-datasource'])('does not recognize inherited datasource key %s', (uid) => {
    expect(modeOfDatasource(uid)).toBeUndefined();
  });
  test('monitoring follows historical versus current window wording', () => {
    expect(show('machines').querySelector('.mon strong')?.textContent).toBe('Monitoring at window end');
    const timeRange = range('now-24h', 'now', [Date.now() - 86400000, Date.now()]);
    expect(show('machines', { mode: 'live', timeRange, data: panelData({ timeRange }) }).querySelector('.mon strong')?.textContent).toBe('Monitoring');
  });
  test('removing the highlighted review row clears its cross-panel highlight', () => {
    const ReadHighlight = () => <output>{useHighlight()?.system ?? 'none'}</output>;
    const output = mount(<ReadHighlight />);
    const view = mount(<HomeView {...props('review')} />);
    const row = view.host.querySelector<HTMLAnchorElement>('a.ex')!;
    act(() => row.focus());
    expect(output.host.textContent).not.toBe('none');
    view.render(null);
    expect(output.host.textContent).toBe('none');
  });
  test('unmounting an older row cannot clear another row highlight', () => {
    const ReadHighlight = () => <output>{useHighlight()?.system ?? 'none'}</output>;
    const output = mount(<ReadHighlight />);
    const first = mount(<HomeView {...props('review')} />);
    const second = mount(<HomeView {...props('review')} />);
    act(() => first.host.querySelector<HTMLAnchorElement>('a.ex')!.dispatchEvent(new MouseEvent('pointerover', { bubbles: true })));
    act(() => second.host.querySelector<HTMLAnchorElement>('a.ex')!.dispatchEvent(new MouseEvent('pointerover', { bubbles: true })));
    first.render(null);
    expect(output.host.textContent).not.toBe('none');
    second.render(null);
    expect(output.host.textContent).toBe('none');
  });
});
