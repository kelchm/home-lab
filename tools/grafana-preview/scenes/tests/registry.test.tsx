// This suite uses the real domain registry; lifecycle suites use explicitly synthetic domains.
import { dateTime, type TimeRange } from '@grafana/data';
import { DOMAINS, domainForFocus } from '@shared/domains/registry';
import { pageRequiredRefs, type DomainContext } from '@shared/domains/contract';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { COMMON_QUERIES, FOCUSES, HOME_URL } from '../src/contract';
import { deeperHref, routeHref } from '../src/urls';
import { installFakes, mountAt, runs, useRecordedData } from './support/harness';

const timeRange: TimeRange = { from: dateTime('2026-11-01T01:27:04.636Z'), to: dateTime('2026-11-02T01:27:04.636Z'), raw: { from: '2026-11-01T01:27:04.636Z', to: '2026-11-02T01:27:04.636Z' } };
const context = (focus: string, view: string, entity: string | null): DomainContext => {
  const location = { pathname: HOME_URL, search: `?focus=${focus}&view=${view}&source=live&from=now-6h&to=now&timezone=utc` };
  return { series: [], timeRange, entity, mode: 'recorded', timeZone: 'UTC', coverage: { freshness: 'unavailable', missing: 1, retained: 0, retainedAt: null, unanswered: true }, href: (route) => routeHref(location, route), deeper: (uid, variables) => deeperHref(location, uid, variables) };
};

beforeEach(() => { installFakes(); useRecordedData(); });
afterEach(() => cleanup());

describe('real domain integration contract', () => {
  it('registers all seven domain destinations in navigation order', () => {
    expect(DOMAINS.map((domain) => domain.focus)).toEqual(FOCUSES.slice(1));
    expect(new Set(DOMAINS.map((domain) => domain.id)).size).toBe(7);
  });

  it('uses globally unique domain refs and bundles query definitions without samples', () => {
    const queries = DOMAINS.flatMap((domain) => domain.queries);
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId).toMatch(/^D_[a-z0-9]+_\w+$/);
      expect(query.expr.length).toBeGreaterThan(0);
      for (const key of ['series', 'samples', 'data', 'values']) expect(query).not.toHaveProperty(key);
    }
  });

  it('can build every real page with missing telemetry and unknown resource identities', () => {
    for (const domain of DOMAINS) {
      expect(new Set(domain.pages.map((page) => page.id)).size).toBe(domain.pages.length);
      for (const page of domain.pages) for (const entity of [null, 'missing-resource']) for (const mode of ['recorded', 'live'] as const) {
        const scope = { ...context(domain.focus, page.id, entity), mode };
        const sections = page.sections(scope);
        expect(sections.length).toBeGreaterThan(0);
        expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
        const refs = new Set(domain.queries.map((query) => query.refId));
        for (const ref of pageRequiredRefs(page, scope)) expect(refs.has(ref), `${domain.focus}/${page.id}: undeclared required ref ${ref}`).toBe(true);
        for (const section of sections) if (section.kind === 'chart') {
          expect(section.refs.length).toBeGreaterThan(0);
          for (const ref of section.refs) expect(refs.has(ref), `${domain.focus}/${page.id}: undeclared chart ref ${ref}`).toBe(true);
        }
      }
    }
  });

  it.each(FOCUSES.slice(1))('mounts %s using only the global source and its own runner', async (focus) => {
    const domain = domainForFocus(focus)!;
    expect(domain).toBeDefined();
    await mountAt(`?focus=${focus}`);
    expect(document.querySelector(`[data-domain="${domain.id}"]`)).not.toBeNull();
    expect(runs).toHaveLength(2);
    expect(runs.flatMap((run) => run.refIds).sort()).toEqual([...COMMON_QUERIES, ...domain.queries].map((query) => query.refId).sort());
    expect(document.querySelectorAll('.domain-section').length).toBeGreaterThan(0);
  });
});
