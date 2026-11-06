import { useMemo, type ReactNode } from 'react';
import type { PanelData, TimeRange } from '@grafana/data';
import { coverage, rangeKey, useResolvedData, type Coverage } from '../lifecycle';
import type { FrameLike } from '../model/semantics';
import { pageRequiredRefs, type DomainCell, type DomainContext, type DomainDefinition, type DomainPage, type DomainSection } from './contract';
import { useDomainStyles } from './styles';

export type ChartSection = Extract<DomainSection, { kind: 'chart' }>;

export interface DomainChartProps {
  section: ChartSection;
  data: PanelData;
  coverage: Coverage;
  entity: string | null;
}

export interface DomainViewProps extends Omit<DomainContext, 'series' | 'coverage'> {
  domain: DomainDefinition;
  page: DomainPage;
  data: PanelData;
  /** The Scenes host supplies real Grafana timeseries panels; this renderer does not simulate a chart. */
  renderChart: (props: DomainChartProps) => ReactNode;
  inspectHref?: string;
}

const stamp = (at: number, zone: string) => new Intl.DateTimeFormat('en-US', { timeZone: zone, dateStyle: 'medium', timeStyle: 'medium' }).format(at);

export function coverageMessage(state: Coverage, zone: string): string | null {
  const retained = state.retainedAt == null ? '' : ` Retained values were received ${stamp(state.retainedAt, zone)} (${zone}) and are dimmed.`;
  if (state.freshness === 'loading') return `Updating this page.${retained}`;
  if (state.freshness === 'stale') return `Some queries have no current answer.${retained}`;
  if (state.freshness === 'failed') return 'Required queries failed. The state of these resources is unknown.';
  if (state.freshness === 'unavailable') return 'Required data is unavailable in this window. Missing observations do not establish an idle or healthy resource.';
  if (state.freshness === 'partial') return `${state.missing} required ${state.missing === 1 ? 'query has' : 'queries have'} no current answer. Coverage is incomplete.`;
  if (state.freshness === 'empty') return 'The required queries returned no observations in this window.';
  return null;
}

function chartCoverageMessage(state: Coverage, zone: string): string | null {
  if (state.retainedAt != null) return `Last received ${stamp(state.retainedAt, zone)} (${zone}); dimmed while ${state.freshness === 'loading' ? 'updating' : 'there is no current answer'}.`;
  if (state.freshness === 'loading') return 'Updating chart…';
  if (state.freshness === 'failed') return 'Query failed; no current observations.';
  if (state.freshness === 'partial') return 'Some chart queries have no current answer.';
  if (state.freshness === 'unavailable') return 'No observations available in this window.';
  if (state.freshness === 'empty') return 'No matching series in this window.';
  return null;
}

const windowLabel = (range: TimeRange, zone: string) => `${stamp(range.from.valueOf(), zone)} – ${stamp(range.to.valueOf(), zone)} · ${zone}`;

function Cell({ value }: { value: string | DomainCell }) {
  if (typeof value === 'string') return <>{value}</>;
  return <span data-tone={value.tone}>{value.href ? <a href={value.href}>{value.text}</a> : value.text}{value.detail && <small className="domain-detail">{value.detail}</small>}</span>;
}

/** Remounting this component at a changed page/entity clears retained scope; range/source changes are also guarded by the resolver. */
export function DomainView(props: DomainViewProps) {
  return <ResolvedDomainView key={`${props.domain.id}:${props.page.id}:${props.entity ?? ''}`} {...props} />;
}

function ResolvedDomainView({ domain, page, data, timeRange, mode, timeZone, entity, href, deeper, renderChart, inspectHref }: DomainViewProps) {
  useDomainStyles();
  const expected = useMemo(() => domain.queries.map((query) => query.refId), [domain]);
  const resolved = useResolvedData(data, expected, `${mode}:${domain.id}`, rangeKey(timeRange));
  const required = useMemo(() => pageRequiredRefs(page, { timeRange, mode, entity }), [page, timeRange, mode, entity]);
  const own = useMemo(() => coverage(resolved, required), [resolved, required]);
  // Optional card values are used only when current. Their absence does not make an unrelated page stale.
  // Native charts receive their own retained refs below and explicitly date those copies.
  const ctx = useMemo<DomainContext>(() => ({
    series: resolved.series.filter((frame) => required.includes(frame.refId ?? '') || resolved.refs.get(frame.refId ?? '')?.state === 'current') as unknown as FrameLike[],
    timeRange, mode, timeZone, entity, coverage: own, href, deeper,
  }), [resolved, required, timeRange, mode, timeZone, entity, own, href, deeper]);
  const sections = useMemo(() => page.sections(ctx), [page, ctx]);
  const chartPanelData = useMemo(() => ({ ...data, timeRange, series: resolved.series }), [data, timeRange, resolved.series]);
  const message = coverageMessage(own, timeZone);
  const retained = own.retained > 0;
  return (
    <article className="lab-domain" data-domain={domain.id} data-page={page.id} data-entity={entity ?? undefined} data-accent={domain.accent} data-mode={mode} data-freshness={own.freshness}>
      <header className="domain-head">
        <div><div className="domain-eyebrow">{domain.title}</div><h2>{page.title}</h2><p>{page.question}</p></div>
        <a className="domain-back" href={href(entity ? { entity: null } : { focus: 'Overview', view: undefined, entity: null })}>{entity ? `All ${domain.title} resources` : 'Overview'}</a>
      </header>
      <nav className="domain-tabs" aria-label={`${domain.title} pages`}>
        {domain.pages.map((item) => <a key={item.id} href={href({ view: item.id, entity: item.id === page.id ? entity : null })} aria-current={item.id === page.id ? 'page' : undefined}>{item.title}</a>)}
      </nav>
      <div className="domain-context"><span>{mode === 'recorded' ? 'Recorded observations' : 'Live source'}</span><span>{windowLabel(timeRange, timeZone)}</span>{entity && <span className="domain-entity">Resource: {entity}</span>}</div>
      {message && <p className="domain-status" role="status">{message}</p>}
      <div className="domain-sections">
        {sections.map((section) => {
          const chartCoverage = section.kind === 'chart' ? coverage(resolved, section.refs) : undefined;
          const chartStatus = chartCoverage && chartCoverageMessage(chartCoverage, timeZone);
          return <section key={section.id} className="domain-section" data-section={section.id} data-kind={section.kind} aria-label={section.title}>
            {section.kind !== 'chart' && <h3>{section.title}</h3>}
            <div className={retained && section.kind !== 'chart' ? 'domain-retained' : undefined} data-retained={retained || undefined}>
              {section.kind === 'text' && <><p data-tone={section.tone}>{section.text}</p>{section.links && <ul className="domain-links">{section.links.map((link) => <li key={link.href}><a href={link.href}>{link.label}</a></li>)}</ul>}</>}
              {section.kind === 'metrics' && <dl className="domain-metrics">{section.items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd data-tone={item.tone}>{item.href ? <a href={item.href}>{item.value}</a> : item.value}</dd>{item.detail && <small className="domain-detail">{item.detail}</small>}</div>)}</dl>}
              {section.kind === 'table' && <>{section.description && <p className="domain-description">{section.description}</p>}{section.rows.length ? <div className="domain-table-wrap" tabIndex={0} role="region" aria-label={`${section.title} table`}><table><thead><tr>{section.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead><tbody>{section.rows.map((row) => <tr key={row.id}>{row.cells.map((value, index) => <td key={index}><Cell value={value} /></td>)}</tr>)}</tbody></table></div> : <p className="domain-empty">{section.empty}</p>}</>}
              {section.kind === 'links' && <ul className="domain-links">{section.items.map((item) => <li key={`${item.label}:${item.href}`}><a href={item.href}>{item.label}</a>{item.description && <span className="domain-detail">{item.description}</span>}</li>)}</ul>}
              {section.kind === 'chart' && <>{renderChart({ section, data: chartPanelData, coverage: chartCoverage!, entity: section.entity ?? entity })}{chartStatus && <p className="domain-chart-state">{chartStatus}</p>}</>}
            </div>
          </section>;
        })}
      </div>
      {inspectHref && <div className="domain-tools"><a href={inspectHref}>Inspect required page queries</a></div>}
    </article>
  );
}
