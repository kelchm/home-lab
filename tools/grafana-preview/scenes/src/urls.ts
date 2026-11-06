// URL state is shared by navigation, drill-in, inspector, sharing and Grafana history.
import { locationUtil, type TimeRange } from '@grafana/data';
import type { DomainRoute } from '@shared/domains/contract';
import { domainForFocus } from '@shared/domains/registry';
import { DATASOURCES, FOCUSES, type Focus, HOME_URL, NATIVE_DASHBOARD_UID, type Source } from './contract';

export interface LocationLike { pathname: string; search: string; hash?: string }
export interface RouteState { focus: Focus; view: string | null; entity: string | null }
export const INSPECT_KEYS = ['inspect', 'inspectTab'] as const;
export const parseSource = (search: string): Source => new URLSearchParams(search).get('source') === 'live' ? 'live' : 'replay';
export const parseFocus = (value: unknown): Focus => FOCUSES.find((focus) => focus === value) ?? 'Overview';

/** Entity identifiers are opaque label values, not HTML, expressions or path fragments. */
export const parseEntity = (value: unknown): string | null =>
  typeof value === 'string' && value.length > 0 && value.length <= 512 && value.trim() === value && !/[\u0000-\u001f\u007f]/.test(value) ? value : null;

export function parseRoute(values: { focus?: unknown; view?: unknown; entity?: unknown }): RouteState {
  const focus = parseFocus(values.focus);
  const domain = domainForFocus(focus);
  if (!domain) return { focus, view: null, entity: null };
  const requested = typeof values.view === 'string' ? values.view : null;
  const page = domain.pages.find((item) => item.id === requested);
  const invalidView = values.view != null && !page;
  return { focus, view: (page ?? domain.pages[0]).id, entity: invalidView ? null : parseEntity(values.entity) };
}

const withQuery = (location: LocationLike, query: Record<string, string | null>) =>
  locationUtil.getUrlForPartial({ ...location, hash: location.hash ?? '', state: undefined }, query);

/** Cross-domain navigation drops only the old page/entity. Same-domain drill-in keeps the rest of the context. */
export function routeHref(location: LocationLike, route: DomainRoute) {
  const previous = new URLSearchParams(location.search);
  const focus = route.focus === undefined ? parseFocus(previous.get('focus')) : parseFocus(route.focus);
  const changedFocus = focus !== parseFocus(previous.get('focus'));
  const next = parseRoute({ focus, view: route.view ?? (changedFocus ? null : previous.get('view')), entity: 'entity' in route ? route.entity : changedFocus ? null : previous.get('entity') });
  const query: Record<string, string | null> = { ...next, inspect: null, inspectTab: null };
  if (route.from !== undefined) query.from = String(route.from);
  if (route.to !== undefined) query.to = String(route.to);
  return withQuery({ ...location, pathname: HOME_URL }, query);
}

export const focusHref = (location: LocationLike, focus: Focus) => routeHref(location, { focus, view: domainForFocus(focus)?.pages[0].id, entity: null });
export const focusLinks = (location: LocationLike): Record<Focus, string> => Object.fromEntries(FOCUSES.map((focus) => [focus, focusHref(location, focus)])) as Record<Focus, string>;

/** Compare sources over the same selected window; direct live entry still defaults to a moving 24 h window. */
export const sourceHref = (location: LocationLike, source: Source) =>
  withQuery(location, { source: source === 'live' ? 'live' : null, refresh: null, inspect: null, inspectTab: null });
export const inspectHref = (location: LocationLike, target: string, tab: string) => withQuery(location, { inspect: target, inspectTab: tab });

export function shareHref(location: LocationLike, range: TimeRange, lockRange: boolean) {
  const params = new URLSearchParams(location.search);
  INSPECT_KEYS.forEach((key) => params.delete(key));
  if (lockRange) {
    params.set('from', range.from.toISOString());
    params.set('to', range.to.toISOString());
    params.delete('refresh');
  }
  return `${HOME_URL}?${params}`;
}

/** Native copies stay on this isolated Grafana and receive the same range, source, zone and resource variables. */
export function deeperHref(location: LocationLike, uid: string, variables: Record<string, string | undefined> = {}) {
  const current = new URLSearchParams(location.search);
  const params = new URLSearchParams();
  for (const key of ['from', 'to', 'timezone', 'source', 'focus', 'view', 'entity']) {
    const value = current.get(key);
    if (value !== null) params.set(key, value);
  }
  for (const [key, value] of Object.entries(variables)) if (value !== undefined) params.set(key.startsWith('var-') ? key : `var-${key}`, value);
  params.set('var-datasource', DATASOURCES[parseSource(location.search)].uid);
  return `/d/${encodeURIComponent(uid)}?${params}`;
}

export function nativeEditHref(panelId: number, focus: Focus, source: Source, rawRange: { from: unknown; to: unknown }, location?: LocationLike) {
  const params = new URLSearchParams({ editPanel: String(panelId), 'var-focus': focus, 'var-datasource': DATASOURCES[source].uid, from: String(rawRange.from), to: String(rawRange.to) });
  if (location) for (const key of ['view', 'entity', 'timezone', 'source']) {
    const value = new URLSearchParams(location.search).get(key);
    if (value !== null) params.set(key, value);
  }
  return `/d/${NATIVE_DASHBOARD_UID}?${params}`;
}
