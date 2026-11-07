import { describe, expect, test } from 'vitest';
import type { Row } from '../src/model/home';
import { reviewTarget } from '../src/views/Review';
import { range } from './fixtures';

describe('review to system evidence', () => {
  const row = { id: 'probe/athena-dsm', system: 'nas' } as Row;
  const ctx = { timeRange: range(), links: { Athena: '/a/homelab-observability-app/explore?focus=Athena&source=live&from=1793496424636&to=1793582824636&timezone=UTC' } };

  test('a resolved DSM incident opens its evidence with the same source and range', () => {
    const target = reviewTarget(row, ctx)!;
    const url = new URL(target.href, 'https://preview.invalid');
    expect(target.outside).toBe(false);
    expect(Object.fromEntries(url.searchParams)).toEqual({ focus: 'Athena', view: 'incident', entity: 'athena-dsm', source: 'live', from: '1793496424636', to: '1793582824636', timezone: 'UTC' });
    expect(target.href).not.toContain('preview.invalid');
  });

  test('other findings keep the system entry point', () => {
    expect(reviewTarget({ ...row, id: 'a-different-finding' }, ctx)?.href).toBe(ctx.links.Athena);
  });

  test('a host without an internal Athena destination is not redirected into an absent app', () => {
    const target = reviewTarget(row, { ...ctx, links: {} });
    expect(target?.href).not.toContain('view=incident');
  });
});
