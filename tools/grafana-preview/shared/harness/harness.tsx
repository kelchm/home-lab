// Renders the real views in a page, from the catalog's recorded points, shaped as Grafana delivers them. Evidence tooling: never bundled into dist.
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { DataFrame, PanelData, TimeRange } from '@grafana/data';
import catalog from '../../runtime/catalog.json';
import { HomeView } from '../src/HomeView';
import type { View } from '../src/types';

interface Entry {
  refId: string;
  expr: string;
  instant: boolean;
  scope: string;
  series: Array<{ labels: Record<string, string>; t: number[]; v: Array<number | null> }>;
}

const params = new URLSearchParams(location.search);
const layout = params.get('layout') ?? 'native';
const focus = params.get('focus') ?? 'Overview';
const fail = params.get('fail') ? new RegExp(params.get('fail')!) : null;
const FROM = 1793496424636;
const TO = 1793582824636;
const REQUESTED_STEP = 300000;

const edge = (ms: number) => ({ valueOf: () => ms, toISOString: () => new Date(ms).toISOString() });
const timeRange = { from: edge(FROM), to: edge(TO), raw: { from: new Date(FROM).toISOString(), to: new Date(TO).toISOString() } } as unknown as TimeRange;

function framesOf(entry: Entry): DataFrame[] {
  const out = entry.series.flatMap((s) => {
    const points = s.t.map((t, i) => [t, s.v[i]] as const).filter(([t, v]) => v != null && (entry.instant || (t >= FROM - REQUESTED_STEP && t <= TO)));
    const kept = entry.instant ? points.slice(-1) : points;
    return kept.length
      ? [{
          refId: entry.refId,
          length: kept.length,
          fields: [
            { name: 'Time', type: 'time', config: { interval: REQUESTED_STEP }, values: kept.map((p) => p[0]) },
            { name: 'Value', type: 'number', labels: s.labels, config: {}, values: kept.map((p) => p[1]) },
          ],
        }]
      : [];
  });
  return (out.length ? out : [{ refId: entry.refId, length: 0, fields: [] }]) as unknown as DataFrame[];
}

const entries = catalog as unknown as Entry[];
const dataFor = (scope: Array<'common' | 'network'>): PanelData => {
  const picked = entries.filter((e) => scope.includes(e.scope as 'common'));
  const failing = picked.filter((e) => fail?.test(e.refId));
  return {
    state: (failing.length ? 'Error' : 'Done') as PanelData['state'],
    series: picked.flatMap((e) => (fail?.test(e.refId) ? ([{ refId: e.refId, length: 0, fields: [] }] as unknown as DataFrame[]) : framesOf(e))),
    errors: failing.length ? failing.map((e) => ({ refId: e.refId, message: 'injected' })) : undefined,
    timeRange,
    request: { targets: picked.map((e) => ({ refId: e.refId, expr: e.expr, datasource: { uid: 'comparison', type: 'prometheus' } })) },
  } as unknown as PanelData;
};

const common = dataFor(['common']);
const both = dataFor(['common', 'network']);

function App() {
  const [links] = useState({ Overview: '?layout=' + layout + '&focus=Overview', Network: '?layout=' + layout + '&focus=Network' });
  const view = (v: View, constrained: boolean, data: PanelData = common) => <HomeView data={data} timeRange={timeRange} view={v} focus={focus} links={links} constrained={constrained} mode="recorded" />;
  const cell = (x: number, y: number, w: number, h: number, node: React.ReactNode) => (
    <div style={{ gridColumn: `${x + 1} / span ${w}`, gridRow: `${y + 1} / span ${h}`, minWidth: 0, minHeight: 0 }}>{node}</div>
  );
  if (layout === 'single') {
    return (
      <div style={{ padding: 12, width: Number(params.get('w') ?? 700), height: Number(params.get('h') ?? 400) }}>
        {view(params.get('view') as View, params.get('constrained') !== 'false', both)}
      </div>
    );
  }
  if (layout === 'native') {
    const grid = { display: 'grid', gridTemplateColumns: 'repeat(24,minmax(0,1fr))', gridAutoRows: 30, gap: 8, padding: 16 };
    const narrow = innerWidth < 769;
    if (narrow) {
      const stack = (h: number, node: React.ReactNode) => <div style={{ height: h * 38 - 8, marginBottom: 8 }}>{node}</div>;
      return (
        <div style={{ padding: 8 }}>
          {stack(3, view('navigation', true))}
          {stack(2, view('briefing', true))}
          {focus === 'Network' ? (
            <>
              {stack(7, view('network-summary', true, both))}
              {stack(16, view('network-inventory', true, both))}
            </>
          ) : (
            <>
              {stack(19, view('activity', true))}
              {stack(19, view('review', true))}
              {stack(18, view('machines', true))}
            </>
          )}
        </div>
      );
    }
    return (
      <div style={grid}>
        {cell(0, 0, 24, 3, view('navigation', true))}
        {cell(0, 3, 24, 2, view('briefing', true))}
        {focus === 'Network' ? (
          <>
            {cell(0, 5, 24, 7, view('network-summary', true, both))}
            {cell(0, 12, 12, 9, <div className="chart-stub">Native time series: WAN latency</div>)}
            {cell(12, 12, 12, 9, <div className="chart-stub">Native time series: WAN receive</div>)}
            {cell(0, 21, 12, 9, <div className="chart-stub">Native time series: WAN transmit</div>)}
            {cell(12, 21, 12, 16, view('network-inventory', true, both))}
          </>
        ) : (
          <>
            {cell(0, 5, 15, 19, view('activity', true))}
            {cell(15, 5, 9, 19, view('review', true))}
            {cell(0, 24, 24, 13, view('checks', true))}
            {cell(0, 37, 24, 18, view('machines', true))}
          </>
        )}
      </div>
    );
  }
  const wide = innerWidth >= 1200;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16 }}>
      {view('navigation', false)}
      {view('briefing', false)}
      {focus === 'Network' ? (
        <>
          {view('network-summary', false, both)}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(340px,1fr))', gap: 12 }}>
            <div className="chart-stub" style={{ height: 280 }}>Native time series: WAN latency</div>
            <div className="chart-stub" style={{ height: 280 }}>Native time series: WAN receive</div>
            <div className="chart-stub" style={{ height: 280 }}>Native time series: WAN transmit</div>
          </div>
          {view('network-inventory', false, both)}
        </>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: wide ? 'minmax(0,2fr) minmax(0,1fr)' : 'minmax(0,1fr)', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
              {view('activity', false)}
              {view('checks', false)}
            </div>
            {view('review', false)}
            <div style={{ gridColumn: '1 / -1', minWidth: 0 }}>{view('machines', false)}</div>
          </div>
        </>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
