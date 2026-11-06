import { useState } from 'react';
import type { DataFrame, DataQueryRequest, PanelData, TimeRange } from '@grafana/data';
import { isCatalogRef, MAY_BE_ABSENT } from './model/catalog';
import type { Freshness } from './types';

/**
 * `failed` carries positive evidence. `unanswered` means a settled run said nothing about this query, which can't separate
 * empty from failed, so it never gets causal wording.
 */
export type RefState = 'pending' | 'current' | 'empty' | 'retained' | 'failed' | 'unanswered';

export interface RefStatus {
  state: RefState;
  /** When the retained copy now standing in was received. */
  at: number | null;
  /** Why a retained copy is in use. */
  because?: 'failed' | 'unanswered';
}

export interface Retained {
  frames: DataFrame[];
  at: number;
  key: string;
}
export type RetainedByRef = ReadonlyMap<string, Retained>;

export interface Resolved {
  /** Current frames, plus retained frames standing in for queries with no current answer. */
  series: DataFrame[];
  refs: ReadonlyMap<string, RefStatus>;
  errors: string[];
  next: RetainedByRef;
}

export interface ResolveContext {
  expected: readonly string[];
  /** Datasource identity as the host knows it. */
  source: string;
  /** Deliberate range identity, see `rangeKey`. */
  range: string;
  /** Query text by ref, for consumers whose request names only the panel they share from. */
  queries?: ReadonlyMap<string, string>;
  now: number;
  /** Per-view provenance for numeric value arrays; Dashboard consumers can relabel old frames with a new request. */
  origins?: WeakMap<object, string>;
}

interface TargetLike {
  refId?: string;
  expr?: string;
  hide?: boolean;
  panelId?: number;
  datasource?: { uid?: string } | null;
}

const refOf = (frame: DataFrame) => frame.refId ?? 'A';
const hasRows = (frame: DataFrame) => frame.fields.some((field) => field.values.length > 0);

/** The dashboard scene clears `field.values` of superseded frames in place, so retention must copy them. */
const copyOf = (frame: DataFrame): DataFrame => ({
  ...frame,
  fields: frame.fields.map((field) => ({ ...field, values: Array.from(field.values) })),
});

const targetsOf = (request?: DataQueryRequest) => (request?.targets ?? []) as unknown as TargetLike[];

/** Refreshing a relative range keeps its identity; moving the range doesn't. */
export function rangeKey(range: TimeRange) {
  const edge = (value: unknown) => {
    if (typeof value === 'string') {
      if (value.startsWith('now')) return value;
      if (/^\d+$/.test(value)) return Number(value);
      const parsed = Date.parse(value);
      return Number.isFinite(parsed) ? parsed : value;
    }
    return Number(value);
  };
  return `${edge(range.raw.from)}|${edge(range.raw.to)}`;
}

/** Scopes, ad hoc filters and scoped variables. Grafana's own `__` variables change with every run or hold scene objects. */
export function scopeKey(request?: DataQueryRequest) {
  const variables = Object.entries(request?.scopedVars ?? {})
    .filter(([name]) => !name.startsWith('__'))
    .map(([name, variable]) => `${name}=${String(variable?.value)}`)
    .sort();
  return JSON.stringify([request?.scopes?.map((scope) => scope.metadata.name) ?? [], request?.filters ?? [], variables]);
}

function identity(ref: string, data: PanelData, context: ResolveContext, scope: string) {
  const target = targetsOf(data.request).find((t) => t.refId === ref);
  const sharedSources = targetsOf(data.request).filter((t) => t.panelId !== undefined && !t.expr)
    .map((t) => [data.request?.dashboardUID ?? '', t.panelId]);
  return [target?.expr ?? context.queries?.get(ref) ?? '?', target?.datasource?.uid ?? '', context.source, JSON.stringify(sharedSources), scope, context.range].join('\u0000');
}

/**
 * Grafana may keep old nonempty frames after a failed run. Explicit errors take precedence over those rows; only a successful
 * ref can supply a current answer. Empty frames separate a successful empty answer from silence. A retained copy stands in only
 * under the same query, datasource, scope and range, so changing any of them turns it into a plain unknown.
 */
export function resolve(data: PanelData, retained: RetainedByRef, context: ResolveContext): Resolved {
  const reported = data.errors?.length ? data.errors : data.error ? [data.error] : [];
  const failedRefs = new Set(reported.flatMap((error) => (error.refId ? [error.refId] : [])));
  // Dashboard-datasource errors can name their source target rather than an original metric ref, which could be any query.
  const wholeRequest = reported.some((error) => !error.refId || !(context.expected.includes(error.refId) || isCatalogRef(error.refId)));
  const settled = data.state === 'Done' || data.state === 'Error' || data.state === 'Streaming';
  const scope = scopeKey(data.request);
  const sharedConsumer = targetsOf(data.request).some((target) => target.panelId !== undefined && !target.expr);
  // Range props can change before Grafana replaces the previous request's settled frames.
  const actualRange = data.request?.range ?? data.timeRange;
  const matchesRange = !actualRange || rangeKey(actualRange) === context.range;

  const series: DataFrame[] = [];
  const refs = new Map<string, RefStatus>();
  const next = new Map(retained);

  for (const ref of context.expected) {
    const frames = data.series.filter((frame) => refOf(frame) === ref);
    const key = identity(ref, data, context, scope);
    const valuesOf = (frame: DataFrame) => frame.fields.filter((field) => field.type === 'number').map((field) => field.values);
    const rows = frames.filter(hasRows).filter((frame) => valuesOf(frame).every((values) => {
      const origin = context.origins?.get(values);
      return origin === undefined || origin === key;
    }));
    const kept = retained.get(ref);
    const usable = kept?.key === key ? kept : undefined;
    const standIn = (state: RefState, because?: RefStatus['because']) => {
      series.push(...(usable?.frames ?? []));
      refs.set(ref, { state, at: usable?.at ?? null, because });
    };
    const gap = (state: 'failed' | 'unanswered') => (usable ? standIn('retained', state) : refs.set(ref, { state, at: null }));

    if (!settled) {
      // A refresh has no current answer yet, even when Grafana leaves the previous arrays in place.
      standIn('pending');
    } else if (!matchesRange) {
      gap('unanswered');
    } else if (failedRefs.has(ref) || wholeRequest) {
      // SceneQueryRunner can spread the last successful data into an error response. Do not re-date its rows.
      gap('failed');
    } else if (rows.length) {
      series.push(...rows);
      refs.set(ref, { state: 'current', at: null });
      next.set(ref, { frames: rows.map(copyOf), at: context.now, key });
      for (const frame of rows) for (const values of valuesOf(frame)) context.origins?.set(values, key);
    } else if ((frames.some(hasRows) || sharedConsumer) && !MAY_BE_ABSENT.has(ref)) {
      // An old frame rejected by provenance, or an empty shared-source frame, does not prove a successful empty query.
      gap('unanswered');
    } else if (frames.length || (data.series.length && MAY_BE_ABSENT.has(ref))) {
      next.delete(ref);
      refs.set(ref, { state: 'empty', at: null });
    } else {
      gap('unanswered');
    }
  }

  return { series, refs, errors: [...new Set(reported.map((error) => error.message ?? 'Query failed'))], next };
}

export interface Coverage {
  freshness: Freshness;
  /** Queries with no current answer and nothing standing in. */
  missing: number;
  /** Queries answered only by a retained copy, including ones waiting on a refresh. */
  retained: number;
  /** Oldest retained copy in use. */
  retainedAt: number | null;
  /** Every gap here rests on silence rather than a reported failure. */
  unanswered: boolean;
}

/** One surface's state, from only the queries its claims rest on. */
export function coverage(resolved: Resolved, refs: readonly string[]): Coverage {
  const statuses = refs.flatMap((ref) => resolved.refs.get(ref) ?? []);
  const count = (...states: RefState[]) => statuses.filter((status) => states.includes(status.state)).length;
  const shown = statuses.filter((status) => status.state === 'current' || status.at != null).length;
  const retained = statuses.filter((status) => status.state === 'retained');
  const gaps = statuses.filter((status) => status.state === 'failed' || status.state === 'unanswered');
  const stale = [...retained, ...statuses.filter((status) => status.state === 'pending' && status.at != null)];
  const silent = [...retained, ...gaps].every((status) => status.state === 'unanswered' || status.because === 'unanswered');

  const freshness: Freshness = count('pending')
    ? 'loading'
    : retained.length
      ? 'stale'
      : gaps.length
        ? shown
          ? 'partial'
          : silent
            ? 'unavailable'
            : 'failed'
        : shown
          ? 'fresh'
          : 'empty';

  return {
    freshness,
    missing: gaps.length,
    retained: stale.length,
    retainedAt: stale.length ? Math.min(...stale.map((status) => status.at ?? Infinity)) : null,
    unanswered: silent,
  };
}

/**
 * A Dashboard-datasource consumer's request names only its source panel, so it can't see what a source panel is running. Source
 * panels record their query text here, which lets a consumer notice an edited query under an unchanged refId.
 */
const known = new Map<string, ReadonlyMap<string, string>>();

// Grafana enriches source requests with dashboardUID/panelId. A Dashboard target's panelId names that source,
// not the consumer's request.panelId. Requests without that identity must not enter a global refId namespace.
const panelKey = (request: DataQueryRequest | undefined, panelId = request?.panelId) =>
  request?.dashboardUID && panelId !== undefined ? JSON.stringify([request.dashboardUID, panelId]) : undefined;

export function rememberQueries(request?: DataQueryRequest) {
  const key = panelKey(request);
  if (key) {
    known.set(key, new Map(targetsOf(request).flatMap((target) => target.refId && target.expr
      ? [[target.refId, JSON.stringify([target.expr, target.datasource?.uid ?? ''])]] : [])));
  }
}

export function sourceQueries(request?: DataQueryRequest): ReadonlyMap<string, string> | undefined {
  const sources = targetsOf(request).filter((target) => target.panelId !== undefined && !target.expr);
  if (!sources.length) return undefined;
  const queries = new Map<string, string>();
  const ambiguous = new Set<string>();
  for (const target of sources) {
    const key = panelKey(request, target.panelId);
    if (!key) continue;
    for (const [ref, query] of known.get(key) ?? []) {
      if (queries.has(ref)) ambiguous.add(ref);
      queries.set(ref, JSON.stringify([key, query]));
    }
  }
  for (const ref of ambiguous) queries.delete(ref);
  return queries;
}

/** Derives state from the previous render, the pattern React documents for this, so nothing reads a ref while rendering. */
export function useResolvedData(data: PanelData, expected: readonly string[], source: string, range: string): Resolved {
  const [state, setState] = useState<{ data: PanelData; expected: readonly string[]; source: string; range: string; resolved: Resolved; origins: WeakMap<object, string> } | null>(null);
  if (state?.data === data && state.expected === expected && state.source === source && state.range === range) {
    return state.resolved;
  }
  rememberQueries(data.request);
  const origins = state?.origins ?? new WeakMap<object, string>();
  const resolved = resolve(data, state?.resolved.next ?? new Map(), { expected, source, range, queries: sourceQueries(data.request), now: Date.now(), origins });
  setState({ data, expected, source, range, resolved, origins });
  return resolved;
}
