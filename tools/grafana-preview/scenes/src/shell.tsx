import { css } from '@emotion/css';
import { type GrafanaTheme2 } from '@grafana/data';
import { useChromeHeaderHeight } from '@grafana/runtime';
import {
  type SceneComponentProps, SceneObjectBase, type SceneObject, type SceneObjectState, SceneObjectUrlSyncConfig, type SceneObjectUrlValues,
  type SceneQueryRunner, type VizPanel,
} from '@grafana/scenes';
import { Dropdown, Menu, ToolbarButton, useStyles2 } from '@grafana/ui';

import { SOURCE_CARDS } from './cards';
import type { DomainBody } from './domain-scene';
import { type Focus, type Source, SOURCE_LABELS } from './contract';
import { useAppLocation } from './hooks';
import { type CompareInspector } from './inspector';
import { type ShareControl } from './share';
import { inspectHref, parseRoute, sourceHref } from './urls';

interface OverviewBodyState extends SceneObjectState {
  activity: SceneObject;
  checks: SceneObject;
  review: SceneObject;
  machines: SceneObject;
}

export class OverviewBody extends SceneObjectBase<OverviewBodyState> {
  public static Component = ({ model }: SceneComponentProps<OverviewBody>) => {
    const styles = useStyles2(getStyles);
    const { activity, checks, review, machines } = model.useState();
    return (
      <div className={styles.overviewFrame}>
        <div className={styles.overview}>
          <div className={styles.lead}>
            <activity.Component model={activity} />
            <checks.Component model={checks} />
          </div>
          <review.Component model={review} />
          <div className={styles.wide}>
            <machines.Component model={machines} />
          </div>
        </div>
      </div>
    );
  };
}

interface NetworkBodyState extends SceneObjectState {
  $data: SceneQueryRunner;
  summary: SceneObject;
  charts: VizPanel[];
  inventory: SceneObject;
}

/** The Network runner is this object's `$data`, so it activates only while this focus is mounted. */
export class NetworkBody extends SceneObjectBase<NetworkBodyState> {
  public static Component = ({ model }: SceneComponentProps<NetworkBody>) => {
    const styles = useStyles2(getStyles);
    const { summary, charts, inventory } = model.useState();
    return (
      <div className={styles.network}>
        <summary.Component model={summary} />
        <div className={styles.charts}>
          {charts.map((chart) => (
            <div key={chart.state.key} className={styles.chart} data-chart={chart.state.key}>
              <chart.Component model={chart} />
            </div>
          ))}
        </div>
        <inventory.Component model={inventory} />
      </div>
    );
  };
}

interface ShellState extends SceneObjectState {
  focus: Focus;
  source: Source;
  view: string | null;
  entity: string | null;
  navigation: SceneObject;
  briefing: SceneObject;
  overview: OverviewBody;
  domains: DomainBody[];
  inspector: CompareInspector;
  share: ShareControl;
  timePicker: SceneObject;
  refreshPicker: SceneObject;
}

/** Owns `focus` and the pinned band. It holds every section as a sibling because a sticky band needs a parent as tall as the page. */
export class CompareShell extends SceneObjectBase<ShellState> {
  protected _urlSync = new SceneObjectUrlSyncConfig(this, { keys: ['focus', 'view', 'entity'] });
  public static Component = ShellRenderer;

  public getUrlState() {
    return { focus: this.state.focus, view: this.state.view, entity: this.state.entity };
  }

  /** Scenes passes only keys that differ from the state. Setting state also rewrites an unknown value in the URL, without a history step. */
  public updateFromUrl(values: SceneObjectUrlValues) {
    const changed = 'focus' in values && values.focus !== this.state.focus;
    this.setState(parseRoute({
      focus: 'focus' in values ? values.focus : this.state.focus,
      view: 'view' in values ? values.view : changed ? null : this.state.view,
      entity: 'entity' in values ? values.entity : changed ? null : this.state.entity,
    }));
  }
}

function ShellRenderer({ model }: SceneComponentProps<CompareShell>) {
  const styles = useStyles2(getStyles);
  const top = useChromeHeaderHeight() ?? 0;
  const { focus, view, entity, source, navigation, briefing, overview, domains, inspector, share, timePicker, refreshPicker } = model.useState();
  const body: SceneObject | undefined = focus === 'Overview' ? overview : domains.find((item) => item.state.domain.focus === focus);
  return (
    <div className={styles.shell} data-host="scenes" data-source={source} data-focus={focus} data-page={view ?? undefined} data-entity={entity ?? undefined}>
      <div className={styles.pinned} style={{ top }} data-testid="pinned-band">
        <div className={styles.bar}>
          <div className={styles.title}>
            <strong>Lab</strong>
            <span>Observability</span>
          </div>
          <SourceSwitch source={source} />
          <div className={styles.controls}>
            <timePicker.Component model={timePicker} />
            <refreshPicker.Component model={refreshPicker} />
            <share.Component model={share} />
            <InspectMenu focus={focus} />
          </div>
        </div>
        <navigation.Component model={navigation} />
      </div>
      <briefing.Component model={briefing} />
      {body && <body.Component key={focus} model={body} />}
      <inspector.Component model={inspector} />
    </div>
  );
}

function SourceSwitch({ source }: { source: Source }) {
  const styles = useStyles2(getStyles);
  const location = useAppLocation();
  return (
    <nav className={styles.sources} aria-label="Data source">
      {(['replay', 'live'] as const).map((item) => (
        <a key={item} href={sourceHref(location, item)} aria-current={item === source ? 'true' : undefined}>
          {SOURCE_LABELS[item]}
        </a>
      ))}
    </nav>
  );
}

/** Only the global briefing and the active focus's named source group are inspectable here. */
function InspectMenu({ focus }: { focus: Focus }) {
  const location = useAppLocation();
  const sources = SOURCE_CARDS.filter((spec) => !spec.focus || spec.focus === focus);
  return (
    <Dropdown
      placement="bottom-end"
      overlay={
        <Menu>
          {sources.map((spec) => (
            <Menu.Item key={spec.id} label={`${spec.title} · ${spec.queries.length} queries`} url={inspectHref(location, spec.id, 'query')} />
          ))}
        </Menu>
      }
    >
      <ToolbarButton icon="info-circle" isOpen={false} tooltip="Inspect the queries behind this page">
        Inspect
      </ToolbarButton>
    </Dropdown>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  shell: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(1.5), width: '100%', minWidth: 0 }),
  pinned: css({
    position: 'sticky',
    zIndex: theme.zIndex.activePanel,
    display: 'flex',
    flexDirection: 'column',
    gap: theme.spacing(1),
    margin: theme.spacing(-1, -2, 0),
    padding: theme.spacing(1, 2),
    background: `linear-gradient(180deg, ${theme.colors.background.canvas} 88%, transparent)`,
    // On a short viewport the band would cover most of the page, so it scrolls away instead.
    '@media (max-height: 560px)': { position: 'static' },
  }),
  bar: css({ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: theme.spacing(1, 2) }),
  title: css({
    display: 'flex',
    alignItems: 'baseline',
    gap: theme.spacing(1),
    whiteSpace: 'nowrap',
    strong: { fontSize: theme.typography.h4.fontSize, fontWeight: theme.typography.fontWeightMedium },
    span: { color: theme.colors.text.secondary, fontSize: theme.typography.bodySmall.fontSize },
    [theme.breakpoints.down('sm')]: { display: 'none' },
  }),
  sources: css({
    display: 'inline-flex',
    border: `1px solid ${theme.colors.border.weak}`,
    borderRadius: theme.shape.radius.default,
    overflow: 'hidden',
    a: {
      padding: theme.spacing(0.5, 1),
      color: theme.colors.text.secondary,
      fontSize: theme.typography.bodySmall.fontSize,
      whiteSpace: 'nowrap',
      '&:hover': { color: theme.colors.text.primary, background: theme.colors.action.hover },
      '&:focus-visible': { outline: `2px solid ${theme.colors.primary.main}`, outlineOffset: -2 },
      '&[aria-current]': { color: theme.colors.text.primary, background: theme.colors.action.selected },
    },
  }),
  controls: css({ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: theme.spacing(1), marginLeft: 'auto', minWidth: 0 }),
  // Measured on the content, not the viewport, so a docked Grafana menu cannot collapse a normal desktop to one column.
  overviewFrame: css({ container: 'home-overview / inline-size' }),
  overview: css({
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 2fr) minmax(300px, 1fr)',
    gap: theme.spacing(1.5),
    '@container home-overview (max-width: 900px)': { gridTemplateColumns: 'minmax(0, 1fr)' },
  }),
  lead: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(1.5), minWidth: 0 }),
  wide: css({ gridColumn: '1 / -1', minWidth: 0 }),
  network: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(1.5) }),
  charts: css({ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(340px, 100%), 1fr))', gap: theme.spacing(1.5) }),
  chart: css({ height: 280, minWidth: 0 }),
});
