import { css } from '@emotion/css';
import { applyFieldOverrides, type DataFrame, FieldType, formatLabels, type GrafanaTheme2, LoadingState, locationUtil, type PanelData, toCSV } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import {
  type SceneComponentProps, type SceneDataProvider, sceneGraph, SceneObjectBase, type SceneObjectRef, type SceneObjectState, SceneObjectUrlSyncConfig, type SceneObjectUrlValues,
} from '@grafana/scenes';
import { Alert, Badge, Button, ClipboardButton, Combobox, Drawer, LinkButton, Tab, Table, TabsBar, useStyles2, useTheme2 } from '@grafana/ui';
import { useMemo, useState } from 'react';

import { access } from './access';
import { HomeCard } from './home-card';
import { type CardSpec, cardById, refIdsOf } from './cards';
import { DATASOURCES, type Focus, type Source } from './contract';
import { chartData, type DomainBody } from './domain-scene';
import { domainForFocus } from '@shared/domains/registry';
import { coverage, type Coverage } from '@shared/lifecycle';
import { coverageMessage } from '@shared/domains/DomainView';
import { useAppLocation, useElementWidth } from './hooks';
import { exploreHref } from './tools';
import { CompareShell } from './shell';
import { inspectHref, nativeEditHref } from './urls';

const TABS = [
  { id: 'data', label: 'Data' },
  { id: 'query', label: 'Query' },
  { id: 'change', label: 'Change' },
];

interface InspectorState extends SceneObjectState {
  target?: string;
  tab?: string;
  sources: Record<string, SceneObjectRef<SceneDataProvider>>;
}

/**
 * A custom substitute for Grafana's panel inspector, which lives in dashboard-scene and is not exposed to plugins.
 * Like the native one it is addressed by the URL, so opening it is a link, Back closes it and it survives reload.
 */
export class CompareInspector extends SceneObjectBase<InspectorState> {
  protected _urlSync = new SceneObjectUrlSyncConfig(this, { keys: ['inspect', 'inspectTab'] });
  public static Component = InspectorRenderer;

  public getUrlState() {
    return { inspect: this.state.target ?? null, inspectTab: this.state.target ? (this.state.tab ?? 'data') : null };
  }

  public updateFromUrl(values: SceneObjectUrlValues) {
    const patch: Partial<InspectorState> = {};
    if ('inspect' in values) {
      patch.target = typeof values.inspect === 'string' ? values.inspect : undefined;
    }
    if ('inspectTab' in values) {
      patch.tab = typeof values.inspectTab === 'string' ? values.inspectTab : undefined;
    }
    this.setState(patch);
  }
}

function InspectorRenderer({ model }: SceneComponentProps<CompareInspector>) {
  const { target, tab, sources } = model.useState();
  const { focus } = sceneGraph.getAncestor(model, CompareShell).useState();
  const basic = cardById(target);
  const group = basic?.sourceGroup ?? target?.split(':')[0];
  const domain = domainForFocus(focus);
  if (!target || !group || !sources[group] || (group !== 'common' && group !== domain?.id)) return null;
  return <InspectorScope key={`${target}:${focus}`} model={model} target={target} tab={TABS.some((item) => item.id === tab) ? tab! : 'data'} />;
}

function InspectorScope({ model, target, tab }: { model: CompareInspector; target: string; tab: string }) {
  const shell = sceneGraph.getAncestor(model, CompareShell);
  const { focus, domains } = shell.useState();
  const body = domains.find((item) => item.state.domain.focus === focus);
  if (cardById(target)?.sourceGroup === 'common') {
    const card = sceneGraph.findObject(shell, (object) => object instanceof HomeCard && object.state.spec.id === target) as HomeCard | undefined;
    return card ? <CommonInspector model={model} target={target} tab={tab} card={card} /> : null;
  }
  return body ? <DomainInspector model={model} target={target} tab={tab} body={body} /> : null;
}

function EvidenceUnavailable({ current, message, onClose }: { current: boolean; message: string; onClose: () => void }) {
  return <Drawer title="Inspect" onClose={onClose}><p role="status">{current ? message : 'Waiting for the selected page evidence.'}</p></Drawer>;
}

function CommonInspector({ model, target, tab, card }: { model: CompareInspector; target: string; tab: string; card: HomeCard }) {
  const { focus, source } = sceneGraph.getAncestor(model, CompareShell).useState();
  const { evidence } = card.useState();
  const { data } = sceneGraph.getData(card).useState();
  const { value: timeRange } = sceneGraph.getTimeRange(model).useState();
  const spec = cardById(target)!;
  if (spec.focus && spec.focus !== focus) return null;
  const current = evidence && evidence.input === data && evidence.timeRange === timeRange && evidence.mode === (source === 'replay' ? 'recorded' : 'live');
  if (!current) return <EvidenceUnavailable current={false} message="" onClose={() => locationService.partial({ inspect: null, inspectTab: null })} />;
  const refs = [...refIdsOf(spec)];
  const scoped = chartData(evidence.data, refs, null);
  return <InspectorDrawer model={model} spec={spec} tab={tab} focus={focus} source={source} data={scoped} coverage={coverage(evidence.resolved, refs)} />;
}

function DomainInspector({ model, target, tab, body }: { model: CompareInspector; target: string; tab: string; body: DomainBody }) {
  const { focus, view, entity, source } = sceneGraph.getAncestor(model, CompareShell).useState();
  const { evidence } = body.useState();
  const { data } = body.state.$data.useState();
  const { value: timeRange } = sceneGraph.getTimeRange(model).useState();
  // A route/range update can render before the page publishes its next evidence. Never expose that previous scope.
  const current = evidence && evidence.input === data && evidence.page === view && evidence.context.entity === entity
    && evidence.context.mode === (source === 'replay' ? 'recorded' : 'live') && evidence.context.timeRange === timeRange;
  const spec = current ? cardById(target, evidence.context) : undefined;
  if (!evidence || !spec || (spec.focus && spec.focus !== focus) || (spec.page && spec.page !== view)) {
    return <EvidenceUnavailable current={Boolean(current)} message="This chart has no definition in the selected page evidence. Choose a chart available in this scope." onClose={() => locationService.partial({ inspect: null, inspectTab: null })} />;
  }
  const refs = [...refIdsOf(spec)];
  const scoped = chartData(evidence.data, refs, spec.entity ?? entity, spec.entityLabels);
  return <InspectorDrawer model={model} spec={spec} tab={tab} focus={focus} source={source} data={scoped} coverage={coverage(evidence.resolved, refs)} />;
}

function InspectorDrawer({ model, spec, tab, focus, source, data, coverage: own }: { model: CompareInspector; spec: CardSpec; tab: string; focus: Focus; source: Source; data?: PanelData; coverage: Coverage }) {
  const styles = useStyles2(getStyles);
  const location = useAppLocation();
  const message = coverageMessage(own, 'UTC');
  return (
    <Drawer
      title={`Inspect: ${spec.title}`}
      subtitle={<Badge color="purple" icon="wrench" text="Custom inspector, not Grafana's" />}
      size="lg"
      onClose={() => locationService.partial({ inspect: null, inspectTab: null })}
      tabs={
        <TabsBar>
          {TABS.map((item) => (
            <Tab key={item.id} label={item.label} active={tab === item.id} href={inspectHref(location, spec.id, item.id)} />
          ))}
        </TabsBar>
      }
    >
      <div className={styles.body} data-testid="compare-inspector">
        {message && <p role="status">{message}</p>}
        {tab === 'data' && <DataTab key={spec.id} data={data} />}
        {tab === 'query' && <QueryTab model={model} spec={spec} data={data} source={source} />}
        {tab === 'change' && <ChangeTab model={model} spec={spec} focus={focus} source={source} />}
      </div>
    </Drawer>
  );
}

const frameLabel = (frame: DataFrame) => {
  const labels = frame.fields.find((field) => field.type === FieldType.number)?.labels;
  return `${frame.refId ?? '?'}${labels ? ` ${formatLabels(labels)}` : ''}`;
};

function DataTab({ data }: { data?: PanelData }) {
  const styles = useStyles2(getStyles);
  const theme = useTheme2();
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const [index, setIndex] = useState(0);
  const frames = data?.series ?? [];
  const frame = frames[Math.min(index, frames.length - 1)];
  const table = useMemo(
    () => frame && applyFieldOverrides({ data: [frame], fieldConfig: { defaults: {}, overrides: [] }, replaceVariables: (value) => value, theme })[0],
    [frame, theme]
  );

  if (!data || data.state === LoadingState.Loading && !frames.length) {
    return <p className={styles.note}>Waiting for the query to finish.</p>;
  }
  if (!frame || !table) {
    return <p className={styles.note}>No frames came back for this scope. A query with no matching series returns nothing, which is different from a value of zero.</p>;
  }

  const download = () => {
    const url = URL.createObjectURL(new Blob([toCSV([frame])], { type: 'text/csv' }));
    const link = Object.assign(document.createElement('a'), { href: url, download: `${frame.refId ?? 'frame'}.csv` });
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={styles.stack}>
      <div className={styles.row}>
        <Combobox
          aria-label="Frame"
          value={Math.min(index, frames.length - 1)}
          options={frames.map((item, position) => ({ value: position, label: frameLabel(item) }))}
          onChange={(option) => setIndex(option.value)}
        />
        <Button variant="secondary" icon="download-alt" onClick={download}>
          CSV
        </Button>
      </div>
      <p className={styles.note}>
        {frames.length} frames in this scope. The table shows raw frames, with no transformations or field overrides.
      </p>
      <div ref={ref} className={styles.table}>
        {width && <Table data={table} width={width} height={420} />}
      </div>
    </div>
  );
}

function QueryTab({ model, spec, data, source }: { model: CompareInspector; spec: CardSpec; data?: PanelData; source: Source }) {
  const styles = useStyles2(getStyles);
  const { canExplore } = access();
  const request = data?.request;
  const started = data?.timeRange;
  const facts: Array<[string, string]> = [
    ['Datasource', DATASOURCES[source].uid],
    ['State', data?.state ?? 'Not run'],
    ['Queries', String(spec.queries.length)],
    ['Frames', String(data?.series.length ?? 0)],
    ['Interval', request?.interval ?? '–'],
    ['Max data points', String(request?.maxDataPoints ?? '–')],
    ['Range', started ? `${started.from.toISOString()} → ${started.to.toISOString()}` : '–'],
    ['Processing', data?.timings?.dataProcessingTime === undefined ? '–' : `${data.timings.dataProcessingTime} ms`],
  ];
  const openInExplore = async (refId: string) => {
    const href = await exploreHref(model, new Set([refId]), data);
    if (href) {
      locationService.push(href);
    }
  };
  return (
    <div className={styles.stack}>
      <dl className={styles.facts}>
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {data?.errors?.map((error, position) => (
        <Alert key={position} severity="error" title={error.refId ? `Query ${error.refId} failed` : 'Query failed'}>
          {error.message}
        </Alert>
      ))}
      {!canExplore && <p className={styles.note}>Explore needs the datasources:explore permission, which Viewers do not have by default.</p>}
      {spec.queries.map((query) => (
        <section key={query.refId} className={styles.query}>
          <header>
            <strong>{query.refId}</strong>
            <span>{query.instant ? 'Instant' : 'Range'}</span>
            <span>{data?.series.filter((frame) => frame.refId === query.refId).length ?? 0} frames</span>
            <div className={styles.queryActions}>
              <ClipboardButton size="sm" variant="secondary" icon="copy" getText={() => query.expr}>
                PromQL
              </ClipboardButton>
              {canExplore && (
                <Button size="sm" variant="secondary" icon="compass" onClick={() => openInExplore(query.refId)}>
                  Explore
                </Button>
              )}
            </div>
          </header>
          <pre>{query.expr}</pre>
        </section>
      ))}
    </div>
  );
}

function ChangeTab({ model, spec, focus, source }: { model: CompareInspector; spec: CardSpec; focus: Focus; source: Source }) {
  const styles = useStyles2(getStyles);
  const { canEdit } = access();
  const { from, to } = sceneGraph.getTimeRange(model).useState();
  const location = useAppLocation();
  return (
    <div className={styles.stack}>
      <p>This page is compiled code, so Grafana's panel editor cannot open here and nothing on this page is saved from the browser.</p>
      <h4 className={styles.heading}>Native dashboard</h4>
      {spec.nativePanelId === undefined ? <p>No matching native panel is registered for this view.</p> : canEdit ? (
        <>
          <p>The matching panel can be edited in Grafana's own editor. Changes stay in the native comparison dashboard and do not reach this page.</p>
          <div>
            <LinkButton variant="secondary" icon="edit" href={locationUtil.assureBaseUrl(nativeEditHref(spec.nativePanelId, focus, source, { from, to }, location))}>
              Open in Grafana's editor
            </LinkButton>
          </div>
        </>
      ) : (
        <p>Your role is read-only, so the native editor is not available. It needs the Editor or Admin role.</p>
      )}
      <h4 className={styles.heading}>This page</h4>
      <p>
        Domain queries live in <code>shared/src/domains</code>; Home queries live in <code>runtime/queries.json</code>. A change means editing those files, rebuilding with <code>pnpm run build</code> and reloading.
      </p>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  body: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(2) }),
  stack: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(1.5), p: { margin: 0 } }),
  row: css({ display: 'flex', gap: theme.spacing(1), alignItems: 'center', '> :first-child': { flex: 1, minWidth: 0 } }),
  note: css({ color: theme.colors.text.secondary, fontSize: theme.typography.bodySmall.fontSize }),
  table: css({ minHeight: 420 }),
  heading: css({ margin: theme.spacing(1, 0, 0), fontSize: theme.typography.h5.fontSize }),
  facts: css({
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
    gap: theme.spacing(1, 2),
    margin: 0,
    dt: { color: theme.colors.text.secondary, fontSize: theme.typography.bodySmall.fontSize, fontWeight: theme.typography.fontWeightRegular },
    dd: { margin: 0, overflowWrap: 'anywhere' },
  }),
  query: css({
    border: `1px solid ${theme.colors.border.weak}`,
    borderRadius: theme.shape.radius.default,
    padding: theme.spacing(1),
    header: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: theme.spacing(1), marginBottom: theme.spacing(1), color: theme.colors.text.secondary, strong: { color: theme.colors.text.primary } },
    pre: { margin: 0, maxHeight: 180, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: theme.typography.bodySmall.fontSize },
  }),
  queryActions: css({ display: 'flex', gap: theme.spacing(0.5), marginLeft: 'auto' }),
});
