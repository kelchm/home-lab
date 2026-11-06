vi.mock('@shared/domains/registry', () => import('./support/domains'));
import { dateTime, type TimeRange } from '@grafana/data';
import { describe, expect, it, vi } from 'vitest';

import queries from '../../runtime/queries.json';
import { COMMON_QUERIES, DEFAULT_RANGE, HOME_URL, NETWORK_QUERIES } from '../src/contract';
import { focusHref, inspectHref, nativeEditHref, parseFocus, parseSource, shareHref, sourceHref } from '../src/urls';

const params = (href: string) => new URLSearchParams(href.split('?')[1]);
const location = (search: string) => ({ pathname: HOME_URL, search });
const range = (from: string, to: string): TimeRange => {
  const [start, end] = [dateTime(from), dateTime(to)];
  return { from: start, to: end, raw: { from: 'now-24h', to: 'now' } };
};

describe('source', () => {
  it('is replay unless the URL says live', () => {
    expect(parseSource('')).toBe('replay');
    expect(parseSource('?source=live')).toBe('live');
    expect(parseSource('?source=anything')).toBe('replay');
  });

  it('is the only default that differs: live moves, replay is the fixed recorded window', () => {
    expect(DEFAULT_RANGE.live).toEqual({ from: 'now-24h', to: 'now' });
    expect(DEFAULT_RANGE.replay).toEqual({ from: '2026-11-01T01:27:04.636Z', to: '2026-11-02T01:27:04.636Z' });
  });
});

describe('focus', () => {
  it('falls back to Overview for anything outside the comparison', () => {
    expect(parseFocus('Network')).toBe('Network');
    expect(parseFocus('Kubernetes')).toBe('Kubernetes');
    for (const value of ['Nope', '', null, undefined, ['Network']]) {
      expect(parseFocus(value)).toBe('Overview');
    }
  });

  it('writes only focus and keeps time, refresh and source', () => {
    const search = '?focus=Overview&from=now-6h&to=now&timezone=utc&refresh=1m&source=live';
    const result = params(focusHref(location(search), 'Network'));
    expect(Object.fromEntries(result)).toEqual({ focus: 'Network', view: 'summary', from: 'now-6h', to: 'now', timezone: 'utc', refresh: '1m', source: 'live' });
  });

  it('closes an open inspector, whose target would not exist on the next focus', () => {
    const result = params(focusHref(location('?focus=Overview&inspect=review&inspectTab=data'), 'Network'));
    expect(result.has('inspect')).toBe(false);
    expect(result.has('inspectTab')).toBe(false);
  });
});

describe('switching source', () => {
  it('keeps the selected window when comparing sources', () => {
    const live = params(sourceHref(location(`?focus=Network&from=${DEFAULT_RANGE.replay.from}&to=${DEFAULT_RANGE.replay.to}`), 'live'));
    expect(Object.fromEntries(live)).toEqual({ focus: 'Network', source: 'live', ...DEFAULT_RANGE.replay });
    const replay = params(sourceHref(location('?focus=Network&source=live&from=now-24h&to=now&refresh=1m'), 'replay'));
    expect(Object.fromEntries(replay)).toEqual({ focus: 'Network', from: 'now-24h', to: 'now' });
  });
});

describe('share', () => {
  const window = range('2026-11-02T10:00:00.000Z', '2026-11-02T12:00:00.000Z');

  it('keeps a moving range when not locked and drops inspector state', () => {
    const result = params(shareHref(location('?focus=Network&source=live&from=now-24h&to=now&refresh=1m&inspect=summary&inspectTab=query'), window, false));
    expect(Object.fromEntries(result)).toEqual({ focus: 'Network', source: 'live', from: 'now-24h', to: 'now', refresh: '1m' });
  });

  it('freezes a moving range when locked', () => {
    const result = params(shareHref(location('?focus=Network&source=live&from=now-24h&to=now&refresh=1m'), window, true));
    expect(Object.fromEntries(result)).toEqual({ focus: 'Network', source: 'live', from: '2026-11-02T10:00:00.000Z', to: '2026-11-02T12:00:00.000Z' });
  });
});

describe('native editor link', () => {
  it('targets one panel at the same focus, source and range', () => {
    const href = nativeEditHref(12, 'Network', 'live', { from: 'now-24h', to: 'now' });
    expect(href.split('?')[0]).toBe('/d/home-compare-native');
    expect(Object.fromEntries(params(href))).toEqual({ editPanel: '12', 'var-focus': 'Network', 'var-datasource': 'comparison-live', from: 'now-24h', to: 'now' });
  });
});

describe('inspector link', () => {
  it('adds only inspector keys', () => {
    const result = params(inspectHref(location('?focus=Overview&from=now-6h&to=now'), 'review', 'query'));
    expect(Object.fromEntries(result)).toEqual({ focus: 'Overview', from: 'now-6h', to: 'now', inspect: 'review', inspectTab: 'query' });
  });
});

describe('query contract', () => {
  it('uses the delivered definitions', () => {
    expect(COMMON_QUERIES).toHaveLength(53);
    expect(NETWORK_QUERIES).toHaveLength(31);
    expect(new Set([...COMMON_QUERIES, ...NETWORK_QUERIES].map((query) => query.refId)).size).toBe(84);
  });

  it('bundles expressions only, never recorded samples', () => {
    for (const query of [...queries.common, ...queries.network]) {
      expect(Object.keys(query).sort()).toEqual(['expr', 'instant', 'key', 'range', 'refId', 'scope', 'source']);
    }
  });
});

describe('resource context', () => {
  it.each([['replay', 'comparison'], ['live', 'comparison-live']])('keeps native copies on the selected %s datasource even when a domain supplies a datasource variable', async (source, uid) => {
    const { deeperHref } = await import('../src/urls');
    const target = params(deeperHref(location(`?source=${source}&from=now-6h&to=now`), 'native-copy', { datasource: 'other', 'var-datasource': 'another', node: undefined }));
    expect(target.get('var-datasource')).toBe(uid);
    expect(target.has('var-node')).toBe(false);
  });

  it('keeps entity, subview and zone in source, inspector, share and native links', async () => {
    const { deeperHref } = await import('../src/urls');
    const current = location('?focus=Sparks&view=inventory&entity=spark-1&source=live&from=now-6h&to=now&timezone=utc');
    for (const href of [sourceHref(current, 'replay'), inspectHref(current, 'spark:inventory', 'query'), shareHref(current, range('2026-11-01T02:00Z', '2026-11-01T03:00Z'), false), deeperHref(current, 'sparks-health', { node: 'spark-1' })]) {
      expect(params(href).get('entity')).toBe('spark-1');
      expect(params(href).get('view')).toBe('inventory');
      expect(params(href).get('timezone')).toBe('utc');
      expect(params(href).get('from')).toBe('now-6h');
    }
    const deeper = deeperHref(current, 'sparks-health', { node: 'spark-1' });
    expect(deeper.startsWith('/d/')).toBe(true);
    expect(params(deeper).get('var-node')).toBe('spark-1');
    expect(params(deeper).get('var-datasource')).toBe('comparison-live');
  });

  it('clears the previous domain scope on an internal focus link', () => {
    const next = params(focusHref(location('?focus=Sparks&view=inventory&entity=spark-1&source=live&from=now-6h&to=now'), 'Kubernetes'));
    expect(next.get('focus')).toBe('Kubernetes');
    expect(next.get('view')).toBe('summary');
    expect(next.has('entity')).toBe(false);
    expect(next.get('source')).toBe('live');
  });
});
