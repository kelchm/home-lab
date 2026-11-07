import { DashboardCursorSync, PageLayoutType } from '@grafana/data';
import { behaviors, EmbeddedScene, SceneApp, SceneAppPage, SceneQueryRunner, SceneRefreshPicker, SceneTimePicker, SceneTimeRange } from '@grafana/scenes';
import { DOMAINS } from '@shared/domains/registry';
import { CARDS } from './cards';
import { APP_ID, COMMON_QUERIES, DATASOURCES, DEFAULT_RANGE, HOME_URL, MAX_DATA_POINTS, MIN_INTERVAL, type QueryDef, REFRESH_INTERVALS, type Source, TIME_ZONE } from './contract';
import { SharedFrames } from './data';
import { DomainBody } from './domain-scene';
import { HomeCard, type ViewSpec } from './home-card';
import { CompareInspector } from './inspector';
import { ShareControl } from './share';
import { CompareShell, OverviewBody } from './shell';
import { toolMenu } from './tools';

export const runner = (source: Source, queries: QueryDef[], minInterval = MIN_INTERVAL) => new SceneQueryRunner({
  datasource: DATASOURCES[source], maxDataPoints: MAX_DATA_POINTS, minInterval,
  queries: queries.map(({ refId, expr, instant, range }) => ({ refId, expr, instant, range, editorMode: 'code', legendFormat: '' })),
});

export function buildHomeScene(source: Source) {
  // The common source supports the persistent daily briefing and navigation. All domain queries are independently scoped.
  const common = runner(source, COMMON_QUERIES);
  // Detail windows can be short (including the separate 65-minute inference recording); keep the same bounded point budget.
  const domainRunners = Object.fromEntries(DOMAINS.map((domain) => [domain.id, runner(source, domain.queries, '15s')]));
  const share = new ShareControl({});
  const commonRef = common.getRef();
  const shareRef = share.getRef();
  const card = (spec: ViewSpec, withMenu = true) => new HomeCard({ spec, $data: new SharedFrames({ sources: [commonRef] }), menu: withMenu ? toolMenu(spec, shareRef) : undefined });
  return new EmbeddedScene({
    $timeRange: new SceneTimeRange({ ...DEFAULT_RANGE[source], timeZone: TIME_ZONE }),
    $data: common,
    $behaviors: [new behaviors.CursorSync({ key: 'lab-observability', sync: DashboardCursorSync.Crosshair })],
    body: new CompareShell({
      focus: 'Overview', view: null, entity: null, source,
      navigation: card({ ...CARDS.common, view: 'navigation' }, false),
      briefing: card(CARDS.briefing),
      overview: new OverviewBody({ activity: card(CARDS.activity), checks: card(CARDS.checks), review: card(CARDS.review), machines: card(CARDS.machines) }),
      domains: DOMAINS.map((domain) => new DomainBody({ domain, $data: domainRunners[domain.id], share: shareRef })),
      inspector: new CompareInspector({ sources: { common: commonRef, ...Object.fromEntries(Object.entries(domainRunners).map(([id, provider]) => [id, provider.getRef()])) } }),
      share,
      timePicker: new SceneTimePicker({}),
      refreshPicker: new SceneRefreshPicker({ intervals: REFRESH_INTERVALS, refresh: '' }),
    }),
  });
}

export const buildApp = (source: Source) => new SceneApp({
  name: APP_ID,
  pages: [new SceneAppPage({ title: 'Lab', subTitle: 'Observability', url: HOME_URL, routePath: 'explore/*', layout: PageLayoutType.Canvas, preserveUrlKeys: ['focus', 'view', 'entity', 'source', 'from', 'to', 'timezone', 'refresh'], getScene: () => buildHomeScene(source) })],
  urlSyncOptions: { updateUrlOnInit: true, createBrowserHistorySteps: true },
});
