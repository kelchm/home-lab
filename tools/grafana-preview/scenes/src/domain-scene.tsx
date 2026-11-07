import { FieldColorModeId, FieldType, LoadingState, type DataFrame, type Field, type PanelData } from '@grafana/data';
import { SceneDataNode, sceneGraph, SceneObjectBase, type SceneComponentProps, type SceneObjectRef, type SceneObjectState, type SceneQueryRunner, VizPanel } from '@grafana/scenes';
import { useCallback, useLayoutEffect, useMemo } from 'react';
import { DomainView, type DomainChartProps, type DomainEvidence } from '@shared/domains/DomainView';
import type { DomainDefinition, DomainPage, DomainRoute } from '@shared/domains/contract';
import { pageForDomain } from '@shared/domains/registry';
import { chartCard, pageCard } from './cards';
import { scopeData } from './data';
import { useAppLocation } from './hooks';
import type { ShareControl } from './share';
import { CompareShell } from './shell';
import { exploreButton, toolMenu } from './tools';
import { deeperHref, inspectHref, routeHref } from './urls';

export const ianaZone = (zone: string) => zone === 'browser' ? Intl.DateTimeFormat().resolvedOptions().timeZone : zone === 'utc' ? 'UTC' : zone;

// Azure, sea and orchid, with lighter companions for multi-series panels. These are colors, not status thresholds.
export const SERIES_COLORS = ['#4DB0FF', '#3FE6C4', '#C685FF', '#8CCBFF', '#84EED9', '#DDAFF4'] as const;

function seriesColor(frame: DataFrame, field: Field, multipleMeasures: boolean): string {
  // Raw identity survives frame ordering, resource filtering, refreshes and datasource display-name changes.
  const labels = field.labels ?? {};
  const entityKey = ['wan', 'ups', 'port', 'instance', 'node', 'host', 'device', 'id', 'name'].find((key) => labels[key]);
  const entity = entityKey === 'port' ? ['port', labels.device ?? labels.instance ?? '', labels.port] : entityKey ? [entityKey, labels[entityKey]] : null;
  // A host/path keeps its hue across single-measure charts; a chart comparing measures also names the metric.
  const identity = JSON.stringify(!multipleMeasures && entity ? entity : [frame.refId ?? frame.name ?? '', field.name, Object.entries(labels).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)]);
  let hash = 2166136261;
  for (let i = 0; i < identity.length; i++) hash = Math.imul(hash ^ identity.charCodeAt(i), 16777619);
  return SERIES_COLORS[(hash >>> 0) % SERIES_COLORS.length];
}

function seriesName(frame: DataFrame, field: Field, multipleMeasures: boolean): string {
  const measure = (frame.refId ?? field.name).replace(/^D_[^_]+_/, '').replace(/History$/, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/\bhttp\b/gi, 'HTTP').replace(/\bcpu\b/gi, 'CPU');
  const structural = new Set(['__name__', 'cluster', 'job', 'platform', 'prometheus', 'prometheus_replica']);
  const identity = Object.entries(field.labels ?? {}).filter(([key]) => !structural.has(key)).map(([key, value]) =>
    ['instance', 'node', 'host', 'wan', 'ups', 'name', 'device', 'model', 'recipe', 'runtime'].includes(key) ? value : `${key}=${value}`
  ).join(' · ');
  return identity ? `${multipleMeasures ? `${measure} · ` : ''}${identity}` : measure;
}

interface DomainBodyState extends SceneObjectState {
  $data: SceneQueryRunner;
  domain: DomainDefinition;
  share: SceneObjectRef<ShareControl>;
  charts?: NativeChartScene[];
  evidence?: DomainEvidence & { page: string; input?: PanelData };
}

/** The runner is owned here, so a focus issues queries only while its body is mounted. */
export class DomainBody extends SceneObjectBase<DomainBodyState> {
  public static Component = DomainBodyRenderer;
}

function DomainBodyRenderer({ model }: SceneComponentProps<DomainBody>) {
  const { domain, share, $data } = model.useState();
  const { view, entity, source } = sceneGraph.getAncestor(model, CompareShell).useState();
  const { data } = $data.useState();
  const range = sceneGraph.getTimeRange(model);
  const { value: timeRange } = range.useState();
  const timeZone = ianaZone(range.getTimeZone());
  const location = useAppLocation();
  const href = useCallback((route: DomainRoute) => routeHref(location, route), [location]);
  const deeper = useCallback((uid: string, variables?: Record<string, string | undefined>) => deeperHref(location, uid, variables), [location]);
  const page = pageForDomain(domain, view);
  const panelData = useMemo(() => data ?? { state: LoadingState.Loading, series: [], timeRange }, [data, timeRange]);
  const onEvidence = useCallback((evidence: DomainEvidence) => model.setState({ evidence: { ...evidence, page: page.id, input: data } }), [model, page.id, data]);
  return <DomainView key={`${domain.id}:${page.id}:${entity ?? ''}`} domain={domain} page={page} data={panelData} timeRange={timeRange} mode={source === 'replay' ? 'recorded' : 'live'} timeZone={timeZone} entity={entity} href={href} deeper={deeper} onEvidence={onEvidence} inspectHref={inspectHref(location, pageCard(domain, page).id, 'query')} renderChart={(props) => <NativeDomainChart key={`${props.section.id}:${entity ?? ''}`} {...props} domain={domain} page={page} owner={model} share={share} />} />;
}

/** Keep entity filtering at the frame boundary; missing labels never become an all-resource fallback. */
export function chartData(data: PanelData, refs: readonly string[], entity: string | null, labels?: readonly string[]): PanelData {
  const scoped = scopeData(data, new Set(refs));
  const from = data.timeRange.from.valueOf();
  const to = data.timeRange.to.valueOf();
  const series = scoped.series
    .filter((frame) => !entity || !labels?.length || frame.fields.some((field) => labels.some((name) => field.labels?.[name] === entity)))
    .map((frame) => {
      const time = frame.fields.find((field) => field.type === 'time');
      const indices = time && Array.from(time.values).flatMap((at, index) => Number(at) >= from && Number(at) <= to ? [index] : []);
      // Grafana can clear superseded panel values in place. Each native panel owns its arrays, independently of the runner/cards.
      return { ...frame, length: indices?.length ?? frame.length, fields: frame.fields.map((field) => ({
        ...field,
        config: { ...field.config, ...(field.type === FieldType.number ? { displayName: seriesName(frame, field, refs.length > 1), color: { mode: FieldColorModeId.Fixed, fixedColor: seriesColor(frame, field, refs.length > 1) } } : {}) },
        values: indices ? indices.map((index) => field.values[index]) : Array.from(field.values),
      })) };
    });
  return { ...scoped, series };
}

export const chartFieldConfig = (unit: string) => ({
  defaults: {
    unit,
    color: { mode: FieldColorModeId.Fixed, fixedColor: SERIES_COLORS[0] },
    custom: { drawStyle: 'line', lineInterpolation: 'linear', lineWidth: 2, fillOpacity: 6, gradientMode: 'opacity', showPoints: 'never', spanNulls: false, axisBorderShow: false },
  },
  overrides: [],
});
export const CHART_OPTIONS = { legend: { showLegend: true, displayMode: 'list', placement: 'bottom', calcs: ['lastNotNull'] }, tooltip: { mode: 'multi', sort: 'none' } };

interface NativeChartState extends SceneObjectState { $data: SceneDataNode; panel: VizPanel }

/** A child scene holds resolved frames and inherits the page time range. It never starts a query runner. */
export class NativeChartScene extends SceneObjectBase<NativeChartState> {
  public static Component = ({ model }: SceneComponentProps<NativeChartScene>) => {
    const { panel } = model.useState();
    return <panel.Component model={panel} />;
  };
}

interface NativeDomainChartProps extends DomainChartProps {
  domain: DomainDefinition;
  page: DomainPage;
  owner: DomainBody;
  share: SceneObjectRef<ShareControl>;
}

export function NativeDomainChart({ section, data, coverage, entity, domain, page, owner, share }: NativeDomainChartProps) {
  const refs = section.refs.join('\u0000');
  const labels = section.entityLabels?.join('\u0000');
  const scoped = useMemo(() => chartData(data, refs.split('\u0000'), entity, labels?.split('\u0000')), [data, refs, entity, labels]);
  const model = useMemo(() => {
    const spec = chartCard(domain, page, section);
    return new NativeChartScene({
      $data: new SceneDataNode({ data: scoped }),
      panel: new VizPanel({ key: spec.id, pluginId: 'timeseries', title: section.title, description: section.description, displayMode: 'transparent', menu: toolMenu(spec, share), headerActions: exploreButton(), options: CHART_OPTIONS, fieldConfig: chartFieldConfig(section.unit) }),
    });
    // A different resource gets a new graph, so its first paint cannot contain the previous resource's frames.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain, page, section.id, section.title, section.unit, refs, labels, entity, share]);
  useLayoutEffect(() => {
    owner.setState({ charts: [...(owner.state.charts ?? []), model] });
    return () => owner.setState({ charts: owner.state.charts?.filter((chart) => chart !== model) });
  }, [model, owner]);
  useLayoutEffect(() => { model.state.$data.setState({ data: scoped }); }, [model, scoped]);
  return <div data-chart={section.id} data-chart-refids={section.refs.join(',')} className={coverage.retained ? 'domain-retained' : undefined} style={{ height: section.height ?? 290, minWidth: 0 }}>{model.parent === owner && <model.Component model={model} />}</div>;
}
