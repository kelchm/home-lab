vi.mock('@shared/domains/registry', () => import('./support/domains'));
import { config, locationService } from '@grafana/runtime';
import { LoadingState } from '@grafana/data';
import { access } from '../src/access';
import { domainForFocus } from '@shared/domains/registry';
import { sceneGraph, VizPanel, VizPanelExploreButton, VizPanelMenu } from '@grafana/scenes';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CARDS } from '../src/cards';
import { HomeCard } from '../src/home-card';
import { ShareControl } from '../src/share';
import { exploreButton, exploreHref } from '../src/tools';
import { back, holdResponses, installFakes, mountAt, navigate, runs, searchParams, setRole, settle } from './support/harness';

type App = Awaited<ReturnType<typeof mountAt>>['app'];

const sceneOf = (app: App) => app.state.pages[0].state.initializedScene!;
const menuOf = (app: App, id: string) => {
  const owner = sceneGraph.findObject(sceneOf(app), (object) => (object instanceof HomeCard && object.state.spec.id === id) || (object instanceof VizPanel && (object.state.key === id || object.state.key === `net:summary:${id}`)));
  return (owner as HomeCard | VizPanel).state.menu as VizPanelMenu;
};

/** Activating a VizPanelMenu is what opening it does. */
const open = async (menu: VizPanelMenu) => {
  menu.activate();
  await settle();
  return menu.state.items!.map((item) => item.text);
};

const exploreState = (href: string) => JSON.parse(new URLSearchParams(href.split('?')[1]).get('left')!);

beforeEach(() => {
  installFakes();
  setRole('Viewer');
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('panel menu', () => {
  it('configures the public VizPanelMenu on charts and on natural-height cards alike', async () => {
    const { app } = await mountAt('?focus=Network');
    for (const id of ['latency', 'receive', 'transmit', 'briefing']) {
      expect(menuOf(app, id)).toBeInstanceOf(VizPanelMenu);
    }
  });

  it('gives a Viewer read-only tools and no Explore', async () => {
    const { app } = await mountAt('?focus=Network');
    expect(await open(menuOf(app, 'latency'))).toEqual(['Inspect data (custom)', 'Inspect query (custom)', 'Share view (custom)', 'Edit (custom)…']);
    expect(exploreButton()).toBeUndefined();
  });

  it('adds Explore for roles that core lets in', async () => {
    setRole('Admin');
    const { app } = await mountAt('?focus=Network');
    expect(await open(menuOf(app, 'latency'))).toContain('Explore');
    expect(exploreButton()).toBeInstanceOf(VizPanelExploreButton);
  });

  it('asks an Admin to pick one query rather than open dozens in Explore', async () => {
    setRole('Admin');
    const { app } = await mountAt('');
    expect(await open(menuOf(app, 'review'))).toContain('Explore one query…');
  });

  it('marks every substitute as custom in its label', async () => {
    const { app } = await mountAt('');
    const labels = await open(menuOf(app, 'machines'));
    expect(labels.filter((label) => /Inspect|Share/.test(label)).every((label) => label.endsWith('(custom)'))).toBe(true);
  });
});

describe('permission evidence', () => {
  it.each(['Editor', 'Admin'])('treats an existing empty permission map as authoritative for %s', (orgRole) => {
    config.bootData = { user: { orgRole, permissions: {} } } as never;
    config.viewersCanEdit = true;
    expect(access()).toEqual({ canExplore: false, canEdit: false });
    config.viewersCanEdit = false;
  });
  it('uses role compatibility only when the permissions map is absent', () => {
    config.bootData = { user: { orgRole: 'Editor' } } as never;
    expect(access()).toEqual({ canExplore: true, canEdit: true });
    config.bootData = { user: { orgRole: 'Editor', permissions: { 'datasources:explore': false, 'dashboards:write': true } } } as never;
    expect(access()).toEqual({ canExplore: false, canEdit: true });
  });
});

describe('Explore', () => {
  it('sends the Network inspector query to Explore, rather than reading the common runner', async () => {
    setRole('Admin');
    await mountAt('?focus=Network&inspect=net:summary:latency&inspectTab=query');
    fireEvent.click(await screen.findByRole('button', { name: /^Explore$/ }));
    await waitFor(() => expect(locationService.getLocation().pathname).toBe('/explore'));
    const state = exploreState(locationService.getLocation().search);
    expect(state.queries.map((query: { refId: string }) => query.refId)).toEqual(['D_net_latency']);
    expect(state.datasource).toBe('comparison');
  });

  it('opens only the chart query, on the active source and range', async () => {
    setRole('Admin');
    const { app } = await mountAt('?source=live&focus=Network&from=now-6h&to=now', 'live');
    const chart = sceneGraph.findObject(sceneOf(app), (object) => object instanceof VizPanel && object.state.key === 'net:summary:latency')!;
    const state = exploreState(await exploreHref(chart, new Set(['D_net_latency'])));
    expect(state.queries.map((query: { refId: string }) => query.refId)).toEqual(['D_net_latency']);
    expect(state.datasource).toBe('comparison-live');
    expect(state.range).toEqual({ from: 'now-6h', to: 'now' });
  });

  it('scopes the stock button too, because the chart data itself is scoped', async () => {
    setRole('Admin');
    const { app } = await mountAt('?focus=Network');
    const chart = sceneGraph.findObject(sceneOf(app), (object) => object instanceof VizPanel && object.state.key === 'net:summary:receive')!;
    expect(sceneGraph.getData(chart).state.data?.request?.targets.map((target) => target.refId)).toEqual(['D_net_receive']);
  });
});

describe('inspector', () => {
  it('uses the page required-ref resolver when the selected recording changes', async () => {
    const page = domainForFocus('Network')!.pages[0];
    const original = page.requiredFor;
    page.requiredFor = ({ mode, timeRange }) => mode === 'recorded' && timeRange.to.valueOf() <= Date.parse('2026-11-01T00:05Z') ? ['D_net_devices'] : page.required;
    try {
      await mountAt('?focus=Network&view=summary&from=2026-10-31T23:00:00.000Z&to=2026-11-01T00:05:00.000Z&inspect=net:summary&inspectTab=query');
      expect(runs.at(-1)?.to).toBe('2026-11-01T00:05:00.000Z');
      expect(await screen.findByText('D_net_devices')).toBeTruthy();
      expect(screen.queryByText('D_net_latency')).toBeNull();
      await navigate('?focus=Network&view=summary&from=2026-11-01T01:27:00.000Z&to=2026-11-02T01:27:00.000Z&inspect=net:summary&inspectTab=query');
      expect(runs.at(-1)?.to).toBe('2026-11-02T01:27:00.000Z');
      expect(await screen.findByText('D_net_latency')).toBeTruthy();
      expect(screen.queryByText('D_net_devices')).toBeNull();
    } finally {
      page.requiredFor = original;
    }
  });

  it.each([LoadingState.Loading, LoadingState.Error])('opens a dynamic chart from the page retained evidence during %s', async (state) => {
    const page = domainForFocus('Network')!.pages[0];
    const sections = page.sections;
    page.sections = (ctx) => ctx.series.some((frame) => frame.refId === 'D_net_latency') ? sections(ctx) : [];
    try {
      await mountAt('?focus=Network');
      const run = runs.find((run) => run.refIds.includes('D_net_latency'))!;
      await act(async () => run.respond({ state, series: [], errors: state === LoadingState.Error ? [{ refId: 'D_net_latency', message: 'Failed refresh' }] : undefined }));
      expect(document.querySelector('[data-chart=latency]')).not.toBeNull();
      await navigate('?focus=Network&inspect=net:summary:latency&inspectTab=data');
      expect(await screen.findByText('Inspect: Synthetic latency')).toBeTruthy();
      expect(await screen.findByText(/1 frames in this scope/)).toBeTruthy();
      expect(screen.getByTestId('compare-inspector').textContent).toContain('Retained values were received');
    } finally { page.sections = sections; }
  });

  it('does not expose old frames after selecting a different range while a query is pending', async () => {
    await mountAt('?focus=Network&inspect=net:summary:latency&inspectTab=data');
    expect(await screen.findByText(/1 frames in this scope/)).toBeTruthy();
    holdResponses();
    await navigate('?focus=Network&inspect=net:summary:latency&inspectTab=data&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z');
    expect(screen.queryByText(/1 frames in this scope/)).toBeNull();
    expect(screen.getByText('Inspect: Synthetic latency')).toBeTruthy();
  });

  it('keeps an explicit unavailable drawer when a dynamic definition no longer exists', async () => {
    const page = domainForFocus('Network')!.pages[0];
    const sections = page.sections;
    page.sections = (ctx) => ctx.series.length ? sections(ctx) : [];
    try {
      holdResponses();
      await mountAt('?focus=Network&inspect=net:summary:latency');
      expect(await screen.findByText(/This chart has no definition in the selected page evidence/)).toBeTruthy();
    } finally { page.sections = sections; }
  });

  it('opens from a link, identifies itself and shows only the scope queries', async () => {
    await mountAt('?inspect=checks&inspectTab=query');
    expect(await screen.findByText(/Custom inspector, not Grafana's/)).toBeTruthy();
    expect(screen.getByText('Inspect: Service checks')).toBeTruthy();
    for (const { refId } of CARDS.checks.queries) {
      expect(screen.getByText(refId)).toBeTruthy();
    }
    expect(screen.queryByText('H_WKA')).toBeNull();
  });

  it('states why a Viewer has no Explore instead of offering a refused link', async () => {
    await mountAt('?inspect=checks&inspectTab=query');
    expect(await screen.findByText(/datasources:explore/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Explore' })).toBeNull();
  });

  it('shows a real table of frames', async () => {
    await mountAt('?inspect=machines&inspectTab=data');
    expect(await screen.findByText(/\d+ frames in this scope/)).toBeTruthy();
  });

  it('measures the table on a reload, when frames arrive after the tab mounts', async () => {
    const observed: Element[] = [];
    const observer = vi.spyOn(ResizeObserver.prototype, 'observe').mockImplementation((element) => void observed.push(element));
    holdResponses();
    await mountAt('?inspect=machines&inspectTab=data');
    expect(await screen.findByText('Waiting for the query to finish.')).toBeTruthy();
    const before = observed.length;
    await act(async () => {
      runs[0].respond();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(await screen.findByText(/\d+ frames in this scope/)).toBeTruthy();
    expect(observed.length).toBeGreaterThan(before);
    observer.mockRestore();
  });

  it('closes with Back, as a history step', async () => {
    await mountAt('');
    await navigate(`?focus=Overview&inspect=review&inspectTab=query`);
    expect(await screen.findByText('Inspect: What happened')).toBeTruthy();
    await back();
    await waitFor(() => expect(screen.queryByText('Inspect: What happened')).toBeNull());
    expect(searchParams().has('inspect')).toBe(false);
  });

  it('stays closed for a Network card while Overview is mounted, and for unknown targets', async () => {
    await mountAt('?focus=Overview&inspect=net:summary:latency');
    expect(screen.queryByTestId('compare-inspector')).toBeNull();
    await navigate('?focus=Overview&inspect=nope');
    expect(screen.queryByTestId('compare-inspector')).toBeNull();
  });

  it('reads the Network runner for a Network card', async () => {
    await mountAt('?focus=Network&inspect=net&inspectTab=query');
    expect(await screen.findByText('Inspect: Network query source')).toBeTruthy();
    expect(screen.getByText('D_net_devices')).toBeTruthy();
  });
});

describe('editing workflow', () => {
  it('tells a Viewer the page is read-only and offers no editor', async () => {
    await mountAt('?inspect=review&inspectTab=change');
    expect(await screen.findByText(/Your role is read-only/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Grafana's editor/ })).toBeNull();
  });

  it('links an Admin to the matching native panel and says edits stay there', async () => {
    setRole('Admin');
    await mountAt('?focus=Overview&inspect=checks&inspectTab=change&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z');
    const link = (await screen.findByRole('link', { name: /Grafana's editor/ })) as HTMLAnchorElement;
    const params = new URL(link.href).searchParams;
    expect(new URL(link.href).pathname).toBe('/d/home-compare-native');
    expect(params.get('editPanel')).toBe('6');
    expect(params.get('var-focus')).toBe('Overview');
    expect(params.get('from')).toBe('2026-11-01T02:00:00.000Z');
    expect(screen.getByText(/do not reach this page/)).toBeTruthy();
  });
});

describe('share', () => {
  const openShare = async (app: App) => {
    const share = sceneGraph.findObject(sceneOf(app), (object) => object instanceof ShareControl) as ShareControl;
    await act(async () => share.setState({ isOpen: true }));
    expect(await screen.findByText(/Custom tool, not Grafana's Share/)).toBeTruthy();
    return (document.getElementById('share-link') as HTMLInputElement).value;
  };

  it('keeps focus, source and locked times for a moving range by default', async () => {
    const { app } = await mountAt('?source=live&focus=Network&from=now-6h&to=now', 'live');
    const url = new URL(await openShare(app));
    expect(url.pathname).toBe('/a/homelab-observability-app/explore');
    expect(url.searchParams.get('focus')).toBe('Network');
    expect(url.searchParams.get('source')).toBe('live');
    expect(url.searchParams.get('from')).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it('recognizes a moving end even when the start is absolute', async () => {
    const { app } = await mountAt('?source=live&from=2026-11-01T00:00:00.000Z&to=now', 'live');
    await openShare(app);
    expect(screen.getByText('This range moves with now. Locking keeps the recipient on the same window.')).toBeTruthy();
    expect(screen.queryByText(/already absolute/)).toBeNull();
  });

  it('can keep a moving range moving', async () => {
    const { app } = await mountAt('?source=live&focus=Network&from=now-6h&to=now', 'live');
    await openShare(app);
    fireEvent.click(document.getElementById('share-lock-range')!);
    const url = new URL((document.getElementById('share-link') as HTMLInputElement).value);
    expect(url.searchParams.get('from')).toBe('now-6h');
  });

  it('is an absolute link for the replay window', async () => {
    const { app } = await mountAt('');
    const url = new URL(await openShare(app));
    expect(url.searchParams.has('source')).toBe(false);
    expect(url.searchParams.get('from')).toBe('2026-11-01T01:27:04.636Z');
  });

  it('leaves the URL alone', async () => {
    const { app } = await mountAt('');
    const before = locationService.getLocation().search;
    await openShare(app);
    expect(locationService.getLocation().search).toBe(before);
  });
});

describe('domain native charts', () => {
  it('has no invented native-editor counterpart for a domain page', async () => {
    setRole('Admin');
    await mountAt('?focus=Network&view=inventory&entity=device-a&inspect=net:inventory&inspectTab=change');
    expect(await screen.findByText(/No matching native panel/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Grafana's editor/ })).toBeNull();
  });

  it('mounts real timeseries VizPanels in the shared scene graph at the page range', async () => {
    const { app } = await mountAt('?focus=Network&from=now-6h&to=now');
    const chart = sceneGraph.findObject(sceneOf(app), (object) => object instanceof VizPanel && object.state.key === 'net:summary:receive') as VizPanel;
    expect(chart.state.pluginId).toBe('timeseries');
    expect(sceneGraph.getTimeRange(chart)).toBe(sceneGraph.getTimeRange(sceneOf(app)));
    expect(sceneGraph.getData(chart).state.data?.series.map((frame) => frame.refId)).toEqual(['D_net_receive']);
  });
});
