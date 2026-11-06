import { useLayoutEffect, useMemo, type ComponentType, type CSSProperties } from 'react';
import type { DataQueryRequest, PanelData, TimeRange } from '@grafana/data';
import { clockFor, spanLabel } from './format';
import { coverage, rangeKey, useResolvedData, type Resolved } from './lifecycle';
import { NEEDS, QUERIES, REVIEW_REFS } from './model/catalog';
import { buildHome } from './model/home';
import { buildNetwork } from './model/network';
import type { FrameLike } from './model/semantics';
import type { Focus, HomeViewProps, Mode, View } from './types';
import { useStyles } from './ui/styles';
import { Activity } from './views/Activity';
import { Briefing } from './views/Briefing';
import { Checks } from './views/Checks';
import type { ViewContext } from './views/context';
import { Machines } from './views/Machines';
import { Navigation } from './views/Navigation';
import { NetworkInventory } from './views/NetworkInventory';
import { NetworkSummary } from './views/NetworkSummary';
import { Review } from './views/Review';
import { FOCUSES } from './types';
import { SYSTEMS } from './model/systems';
import { statusOf } from './model/status';
import { Fit, Icon, Mark } from './ui/parts';
import { useDomainStyles } from './domains/styles';

export type { Focus, Freshness, HomeViewProps, Mode, PanelOptions, View } from './types';
export { VIEWS } from './types';

export interface HomeEvidence {
  data: PanelData;
  resolved: Resolved;
  mode: Mode | 'unknown';
  timeRange: TimeRange;
}

const DEFAULT_ZONE = 'America/New_York';
/** How close a live window's end must be to now for its figures to read as current. */
const CURRENT_WITHIN = 5 * 60_000;

const RENDER: Record<View, ComponentType<{ ctx: ViewContext }>> = {
  navigation: Navigation,
  briefing: Briefing,
  activity: Activity,
  review: Review,
  machines: Machines,
  checks: Checks,
  'network-summary': NetworkSummary,
  'network-inventory': NetworkInventory,
};

/** Full-preview navigation keeps the accepted visual/status vocabulary without the legacy host's external badges. */
function InternalNavigation({ ctx }: { ctx: ViewContext }) {
  return <Fit constrained={ctx.constrained} label="Systems"><nav className="nav" aria-label="Systems">{SYSTEMS.map((system) => {
    const status = statusOf(ctx.model.review, system.id, ctx.trusted, ctx.loading);
    return <a key={system.id} className="sys" data-system={system.name} href={ctx.links[system.name]} aria-label={`${system.name}, ${status.text}`} aria-current={system.name === ctx.focus ? 'page' : undefined} style={{ '--s': `var(--${system.accent})` } as CSSProperties}>
      <Icon name={system.icon} /><span className="t"><b>{system.name}</b><small>{status.text}</small></span><Mark kind={status.mark} />
    </a>;
  })}</nav></Fit>;
}

const MODES: Record<string, Mode> = { comparison: 'recorded', 'comparison-live': 'live' };

export const modeOfDatasource = (uid?: string): Mode | undefined => (uid && Object.hasOwn(MODES, uid) ? MODES[uid] : undefined);

/** Consumers of a shared source name only that source here, so a host that knows better passes `mode`. */
function detectMode(request?: DataQueryRequest): Mode | undefined {
  const targets = (request?.targets ?? []) as Array<{ datasource?: { uid?: string } | null }>;
  return targets.map((target) => modeOfDatasource(target.datasource?.uid)).find(Boolean);
}

function describeMode(mode: Mode | 'unknown', current: boolean, span: string) {
  if (mode === 'recorded') {
    return { modeLabel: 'Recorded replay', modeTitle: 'A recorded window, replayed. Nothing here is live.', tag: 'Recorded' };
  }
  if (mode === 'live') {
    return current ? { modeLabel: 'Live', modeTitle: `Live queries over the last ${span}.`, tag: '' } : { modeLabel: 'Live source · past window', modeTitle: 'Live queries over a window that has already ended.', tag: 'Past window' };
  }
  return { modeLabel: 'Source unknown', modeTitle: 'This view can’t tell whether its data is recorded or live.', tag: 'Source unknown' };
}

export function HomeView({ data, timeRange, view, focus, links, constrained = false, width, mode, timeZone = DEFAULT_ZONE, onEvidence }: HomeViewProps & { onEvidence?: (evidence: HomeEvidence) => void }) {
  useStyles();
  useDomainStyles();
  const internal = SYSTEMS.every((system) => Boolean(links[system.name]));
  const source = mode ?? detectMode(data.request) ?? 'unknown';
  const resolved = useResolvedData(data, QUERIES[view], source, rangeKey(timeRange));
  const evidence = useMemo<HomeEvidence>(() => ({ data: { ...data, timeRange, series: resolved.series }, resolved, mode: source, timeRange }), [data, timeRange, resolved, source]);
  useLayoutEffect(() => { onEvidence?.(evidence); }, [onEvidence, evidence]);
  const from = timeRange.from.valueOf();
  const to = timeRange.to.valueOf();
  const own = coverage(resolved, NEEDS[view]);
  const review = coverage(resolved, REVIEW_REFS);

  const series = resolved.series as unknown as FrameLike[];
  const model = useMemo(() => buildHome(series, from, to, { missing: review.missing, retained: review.retained, total: REVIEW_REFS.length }), [series, from, to, review.missing, review.retained]);
  const network = useMemo(() => (view.startsWith('network') ? buildNetwork(series, model.wans) : null), [view, series, model.wans]);

  const current = source === 'live' && Math.abs(Date.now() - to) <= CURRENT_WITHIN;
  const span = spanLabel(to - from);
  const ctx: ViewContext = {
    model,
    network,
    resolved,
    coverage: own,
    trusted: review.freshness === 'fresh',
    loading: review.freshness === 'loading',
    clock: clockFor(timeZone),
    from,
    to,
    span,
    atEnd: current ? '' : ' at window end',
    mode: source,
    ...describeMode(source, current, span),
    links,
    constrained,
    focus: FOCUSES.includes(focus as Focus) ? focus as Focus : 'Overview',
    timeRange,
  };

  const Body = view === 'navigation' && internal ? InternalNavigation : RENDER[view];
  return (
    <div className="hlc" data-view={view} data-internal={internal || undefined} data-freshness={own.freshness} data-mode={source} data-constrained={constrained || undefined} data-width={width} data-errored={Boolean(data.error || data.errors?.length) || undefined} data-range={rangeKey(timeRange)} data-result-range={rangeKey(data.timeRange)} data-request-range={data.request?.range ? rangeKey(data.request.range) : undefined}>
      <Body ctx={ctx} />
    </div>
  );
}
