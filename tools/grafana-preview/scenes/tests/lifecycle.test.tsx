vi.mock('@shared/domains/registry', () => import('./support/domains'));
import { LoadingState } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { cleanup } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { COMMON_QUERIES, DEFAULT_RANGE } from '../src/contract';
import { lastProps } from './support/HomeViewStub';
import { back, forward, historyLength, holdResponses, installFakes, mountAt, mountRoot, navigate, runs, searchParams, settle } from './support/harness';

import { DOMAINS, domainForFocus } from './support/domains';
const NETWORK_QUERIES = domainForFocus('Network')!.queries;
const domainElement = () => document.querySelector<HTMLElement>('[data-domain=net]');

const common = () => runs.filter((run) => run.refIds.includes('H_A'));
const network = () => runs.filter((run) => run.refIds.includes('D_net_latency'));

const clickRefresh = async (view: { container: HTMLElement }) => {
  const button = view.container.querySelector<HTMLButtonElement>('[data-testid="data-testid RefreshPicker run button"], button[aria-label="Refresh"]');
  expect(button).not.toBeNull();
  await act(async () => {
    button!.click();
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
};

beforeEach(() => {
  installFakes();
  lastProps.clear();
});

afterEach(() => cleanup());

describe('first load', () => {
  it('runs the common source once, with every definition, and no Network query', async () => {
    await mountAt('');
    expect(common()).toHaveLength(1);
    expect(common()[0].refIds).toEqual(COMMON_QUERIES.map((query) => query.refId));
    expect(common()[0].uid).toBe('comparison');
    expect(common()[0].maxDataPoints).toBe(289);
    expect(network()).toHaveLength(0);
  });

  it('defaults to the recorded window and writes state into the URL without a new history step', async () => {
    const before = historyLength();
    await mountAt('');
    expect(common()[0].from).toBe(DEFAULT_RANGE.replay.from);
    expect(common()[0].to).toBe(DEFAULT_RANGE.replay.to);
    expect(searchParams().get('focus')).toBe('Overview');
    expect(historyLength()).toBe(before + 1);
  });

  it('gives each shared view the same frames from one run', async () => {
    await mountAt('');
    for (const view of ['navigation', 'briefing', 'activity', 'review', 'machines', 'checks'] as const) {
      expect(lastProps.get(view)?.data.series).toHaveLength(COMMON_QUERIES.length);
      expect(lastProps.get(view)?.constrained).toBe(false);
      expect(lastProps.get(view)?.focus).toBe('Overview');
    }
    expect(lastProps.has('network-summary')).toBe(false);
  });

  it('tells views which source and zone they are drawing', async () => {
    await mountAt('');
    expect(lastProps.get('review')?.mode).toBe('recorded');
    expect(lastProps.get('review')?.timeZone).toBe('America/New_York');
  });
});

describe('focus', () => {
  it('starts Network queries on first visit, with no common re-run', async () => {
    await mountAt('');
    await navigate('?focus=Network');
    expect(network()).toHaveLength(1);
    expect(network()[0].refIds).toEqual(NETWORK_QUERIES.map((query) => query.refId));
    expect(common()).toHaveLength(1);
  });

  it('gives a domain its own resolved frames after common finished', async () => {
    await mountAt('');
    await navigate('?focus=Network');
    expect(domainElement()?.dataset.freshness).toBe('fresh');
    expect(lastProps.get('navigation')?.data.series).toHaveLength(COMMON_QUERIES.length);
  });

  it('costs nothing to return to, or to go Back and Forward through', async () => {
    await mountAt('');
    await navigate('?focus=Network');
    await navigate('?focus=Overview');
    await navigate('?focus=Network');
    await back();
    expect(searchParams().get('focus')).toBe('Overview');
    expect(lastProps.get('review')?.focus).toBe('Overview');
    await forward();
    expect(searchParams().get('focus')).toBe('Network');
    expect(domainElement()?.dataset.page).toBe('summary');
    expect(network()).toHaveLength(1);
    expect(common()).toHaveLength(1);
  });

  it('cancels a Network query when the focus is left before it finishes', async () => {
    await mountAt('');
    holdResponses();
    await navigate('?focus=Network');
    expect(network()[0].cancelled).toBe(false);
    await navigate('?focus=Overview');
    expect(network()[0].cancelled).toBe(true);
    await navigate('?focus=Network');
    expect(network()).toHaveLength(2);
  });

  it('never loads Network for an unknown focus, and corrects the URL in place', async () => {
    const before = historyLength();
    await mountAt('?focus=Nope');
    expect(searchParams().get('focus')).toBe('Overview');
    expect(historyLength()).toBe(before + 1);
    expect(lastProps.get('review')?.focus).toBe('Overview');
    expect(network()).toHaveLength(0);
  });

  it('opens a registered system internally without another focus query', async () => {
    await mountAt('?focus=Kubernetes');
    expect(searchParams().get('focus')).toBe('Kubernetes');
    expect(runs.filter((run) => run.refIds.includes('D_k8s_latency'))).toHaveLength(1);
    expect(network()).toHaveLength(0);
  });

  it('corrects an unknown focus reached while the page is open, from a mounted Network focus', async () => {
    await mountAt('?focus=Network');
    await navigate('?focus=Nope');
    expect(searchParams().get('focus')).toBe('Overview');
    expect(lastProps.get('review')?.focus).toBe('Overview');
  });
});

describe('deep links and reload', () => {
  it('opens a Network link directly, including its time range, with both runners', async () => {
    await mountAt('?focus=Network&from=2026-11-01T10:00:00.000Z&to=2026-11-01T12:00:00.000Z');
    expect(common()).toHaveLength(1);
    expect(network()).toHaveLength(1);
    expect(network()[0].from).toBe('2026-11-01T10:00:00.000Z');
    expect(common()[0].to).toBe('2026-11-01T12:00:00.000Z');
  });
});

describe('links', () => {
  it('keep the current range and write only focus', async () => {
    await mountAt('?from=2026-11-01T10:00:00.000Z&to=2026-11-01T12:00:00.000Z&refresh=1m');
    const links = lastProps.get('navigation')!.links;
    const params = new URLSearchParams(links.Network.split('?')[1]);
    expect(params.get('focus')).toBe('Network');
    expect(params.get('from')).toBe('2026-11-01T10:00:00.000Z');
    expect(params.get('refresh')).toBe('1m');
  });

  it('follow a later range change', async () => {
    await mountAt('');
    await navigate('?focus=Overview&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z');
    const params = new URLSearchParams(lastProps.get('navigation')!.links.Network.split('?')[1]);
    expect(params.get('from')).toBe('2026-11-01T02:00:00.000Z');
  });
});

describe('time and refresh', () => {
  it('reruns only mounted queries on a range change, and catches up on return', async () => {
    await mountAt('?focus=Network');
    await navigate('?focus=Overview');
    const [commonBefore, networkBefore] = [common().length, network().length];
    await navigate('?focus=Overview&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z');
    expect(common()).toHaveLength(commonBefore + 1);
    expect(network()).toHaveLength(networkBefore);
    await navigate('?focus=Network&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z');
    expect(network()).toHaveLength(networkBefore + 1);
    expect(network().at(-1)!.from).toBe('2026-11-01T02:00:00.000Z');
    expect(common()).toHaveLength(commonBefore + 1);
  });

  it('refreshes through Grafana controls, without touching an unmounted focus', async () => {
    const { view } = await mountAt('');
    await clickRefresh(view);
    expect(common()).toHaveLength(2);
    expect(network()).toHaveLength(0);
  });
});

describe('live source', () => {
  it('queries comparison-live over a moving window and keeps source=live through focus changes', async () => {
    await mountAt('?source=live', 'live');
    expect(common()[0].uid).toBe('comparison-live');
    expect(lastProps.get('review')?.mode).toBe('live');
    expect(searchParams().get('from')).toBe('now-24h');
    expect(searchParams().get('source')).toBe('live');
    const link = new URLSearchParams(lastProps.get('navigation')!.links.Network.split('?')[1]);
    expect(link.get('source')).toBe('live');
    await navigate('?source=live&focus=Network&from=now-6h&to=now');
    expect(network()[0].uid).toBe('comparison-live');
    expect(searchParams().get('source')).toBe('live');
  });
});

describe('changing source through the plugin root', () => {
  it('rebuilds the scene, so replay frames never sit under a live label', async () => {
    await mountRoot('');
    expect(common().at(-1)!.uid).toBe('comparison');
    const replayFrames = lastProps.get('review')!.data;
    await navigate('?source=live&from=now-24h&to=now&focus=Overview');
    expect(common().at(-1)!.uid).toBe('comparison-live');
    expect(lastProps.get('review')!.data).not.toBe(replayFrames);
    expect(lastProps.get('review')!.data.request?.targets[0].datasource?.uid).toBe('comparison-live');
  });

  it('sends the bare app URL to Home', async () => {
    await mountRoot('');
    await act(async () => {
      locationService.push('/a/homelab-observability-app');
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(locationService.getLocation().pathname).toBe('/a/homelab-observability-app/explore');
  });

  it('follows Back across a source change', async () => {
    await mountRoot('');
    await navigate('?source=live&from=now-24h&to=now&focus=Overview');
    await back();
    await settle();
    expect(common().at(-1)!.uid).toBe('comparison');
    expect(searchParams().has('source')).toBe(false);
    expect(common().at(-1)!.from).toBe(DEFAULT_RANGE.replay.from);
  });
});

describe('shared status', () => {
  it('passes a failed common query to every view rather than a blank', async () => {
    holdResponses();
    await mountAt('');
    await act(async () => {
      common()[0].respond({ state: LoadingState.Error, errors: [{ message: 'upstream unavailable', refId: 'H_A' }] });
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await settle();
    for (const view of ['navigation', 'briefing', 'review'] as const) {
      expect(lastProps.get(view)?.data.state).toBe(LoadingState.Error);
      expect(lastProps.get(view)?.data.errors?.[0].message).toBe('upstream unavailable');
    }
  });

  it('shows Loading before the first answer, not an empty success', async () => {
    holdResponses();
    await mountAt('');
    expect(lastProps.get('review')?.data.state).toBe(LoadingState.Loading);
  });
});

describe('domain pages and resource routes', () => {
  it.each(DOMAINS.map((domain) => [domain.focus, domain.id] as const))('direct %s entry runs only common plus that focus', async (focus, id) => {
    await mountAt(`?focus=${focus}&view=inventory&entity=device-a&timezone=utc`);
    expect(runs).toHaveLength(2);
    expect(runs.find((run) => run.refIds[0].startsWith('D_'))?.refIds).toEqual(domainForFocus(focus)!.queries.map((query) => query.refId));
    const page = document.querySelector<HTMLElement>(`[data-domain=${id}]`)!;
    expect(page.dataset).toMatchObject({ page: 'inventory', entity: 'device-a' });
    expect(searchParams().get('timezone')).toBe('utc');
  });

  it('keeps page, entity and time through Back/Forward without rerunning a focus', async () => {
    const selected = 'focus=Network&view=inventory&entity=device-a&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z&timezone=utc';
    await mountAt(`?${selected}`);
    await navigate('?focus=Network&view=summary&from=2026-11-01T02:00:00.000Z&to=2026-11-01T03:00:00.000Z&timezone=utc');
    await back();
    expect(domainElement()?.dataset).toMatchObject({ page: 'inventory', entity: 'device-a' });
    expect(searchParams().get('from')).toBe('2026-11-01T02:00:00.000Z');
    await forward();
    expect(domainElement()?.dataset.page).toBe('summary');
    expect(domainElement()?.dataset.entity).toBeUndefined();
    expect(network()).toHaveLength(1);
  });

  it('normalizes an invalid page and malformed entity without a new history step', async () => {
    const before = historyLength();
    await mountAt('?focus=Network&view=not-a-page&entity=device-a');
    expect(searchParams().get('view')).toBe('summary');
    expect(searchParams().has('entity')).toBe(false);
    expect(historyLength()).toBe(before + 1);
    await navigate('?focus=Network&view=inventory&entity=%00device-a');
    expect(searchParams().get('view')).toBe('inventory');
    expect(searchParams().has('entity')).toBe(false);
  });

  it('keeps a valid but unobserved resource identity, rather than silently showing all resources', async () => {
    await mountAt('?focus=Network&view=inventory&entity=missing-device');
    expect(domainElement()?.dataset.entity).toBe('missing-device');
    expect(domainElement()?.textContent).toContain('missing-device');
  });

  it('retains route and window through a source rebuild and Back', async () => {
    const route = 'focus=Sparks&view=inventory&entity=device-a&from=2026-10-31T23:00:00.000Z&to=2026-11-01T00:05:00.000Z&timezone=utc';
    await mountRoot(`?${route}`);
    await navigate(`?${route}&source=live`);
    expect(runs.at(-1)?.uid).toBe('comparison-live');
    expect(document.querySelector<HTMLElement>('[data-domain=spark]')?.dataset).toMatchObject({ page: 'inventory', entity: 'device-a', mode: 'live' });
    await back();
    expect(document.querySelector<HTMLElement>('[data-domain=spark]')?.dataset.mode).toBe('recorded');
    expect(searchParams().get('from')).toBe('2026-10-31T23:00:00.000Z');
    expect(searchParams().get('entity')).toBe('device-a');
  });

  it('limits error coverage to the current page and dates retained data on a failed refresh', async () => {
    holdResponses();
    await mountAt('?focus=Network&view=summary');
    await act(async () => network()[0].respond());
    expect(domainElement()?.dataset.freshness).toBe('fresh');
    await act(async () => network()[0].respond({ state: LoadingState.Error, series: [], errors: [{ refId: 'D_net_devices', message: 'inventory failed' }] }));
    // The summary has no current latency result either, so its old result is retained, with a timestamp.
    expect(domainElement()?.dataset.freshness).toBe('stale');
    expect(domainElement()?.textContent).toContain('Retained values were received');
    await navigate('?focus=Network&view=inventory');
    expect(domainElement()?.dataset.freshness).toBe('failed');
    expect(domainElement()?.textContent).not.toContain('Retained values were received');
    await act(async () => network()[0].respond());
    expect(domainElement()?.dataset.freshness).toBe('fresh');
  });
});
