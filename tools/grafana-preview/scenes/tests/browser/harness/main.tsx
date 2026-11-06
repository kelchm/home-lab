// A browser harness for layout and tools when root's Grafana is not running. Real React, @grafana/ui, Scenes and shell; fixture frames arrive
// through the same runRequest seam Grafana uses. It has no Grafana chrome and no real timeseries plugin, so it proves layout, not integration.
import { createTheme, type DataQueryRequest, LoadingState, PanelPlugin, type PanelData } from '@grafana/data';
import { config, LocationServiceProvider, locationService, setChromeHeaderHeightHook, setDataSourceSrv, setPluginImportUtils, setReturnToPreviousHook, setRunRequest } from '@grafana/runtime';
import { GlobalStyles, ThemeContext } from '@grafana/ui';
import { type ReactNode, StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { Route, Router, Routes } from 'react-router-dom';
import { delay, Observable, of, startWith } from 'rxjs';

import { HOME_URL } from '../../../src/contract';
import { useAppLocation } from '../../../src/hooks';
import { buildApp } from '../../../src/scene';
import { parseSource } from '../../../src/urls';
import { recordedFrames } from '../../support/recorded';

const failing = () => new URLSearchParams(window.location.search).get('fail');

const respond = (request: DataQueryRequest): PanelData => {
  const series = recordedFrames(request);
  const refused = failing();
  const errors = refused ? request.targets.filter((target) => target.refId === refused).map((target) => ({ refId: target.refId, message: 'Injected harness failure' })) : [];
  return {
    state: errors.length ? LoadingState.Error : LoadingState.Done,
    series: series.filter((frame) => frame.refId !== refused),
    errors: errors.length ? errors : undefined,
    request,
    timeRange: request.range,
    timings: { dataProcessingTime: 12 },
  };
};

const datasource = (uid: string) => ({ uid, type: 'prometheus', name: uid, getRef: () => ({ uid, type: 'prometheus' }), query: () => new Observable() });

config.theme2 = createTheme({ colors: { mode: 'dark' } });
config.bootData = { user: { orgRole: new URLSearchParams(window.location.search).get('role') ?? 'Viewer', permissions: {}, timezone: 'browser', weekStart: 'browser' } } as never;
config.exploreEnabled = true;
setChromeHeaderHeightHook(() => 48);
setReturnToPreviousHook(() => () => {});
setDataSourceSrv({
  get: async (ref: string | { uid?: string }) => datasource(typeof ref === 'string' ? ref : (ref?.uid ?? 'comparison')),
  getInstanceSettings: (ref: string | { uid?: string }) => {
    const uid = typeof ref === 'string' ? ref : (ref?.uid ?? 'comparison');
    return { uid, name: uid, type: 'prometheus', meta: { id: 'prometheus' } };
  },
  getList: () => [],
  reload: async () => {},
  registerRuntimeDataSource: () => {},
} as never);
setRunRequest((_datasource, request) => of(respond(request)).pipe(delay(60), startWith({ state: LoadingState.Loading, series: [], request, timeRange: request.range } as PanelData)));

function StandInChart({ width, height, data }: { width: number; height: number; data: PanelData }) {
  return <div style={{ width, height, display: 'grid', placeItems: 'center', opacity: 0.6 }}>Harness stand-in, {data.series.length} frames</div>;
}
const standIn = new PanelPlugin(StandInChart as never);
standIn.meta = { id: 'timeseries', name: 'Time series', info: { version: '0', logos: {} } } as never;
setPluginImportUtils({ importPanelPlugin: async () => standIn, getPanelPluginFromCache: () => standIn } as never);

function ServiceRouter({ children }: { children: ReactNode }) {
  const location = useAppLocation();
  return (
    <Router location={location} navigator={locationService.getHistory() as never}>
      {children}
    </Router>
  );
}

const apps = new Map<string, ReturnType<typeof buildApp>>();
const appFor = (source: 'replay' | 'live') => {
  if (!apps.has(source)) {
    apps.set(source, buildApp(source));
  }
  return apps.get(source)!;
};

function Home() {
  const { search } = useAppLocation();
  const source = parseSource(search);
  const app = appFor(source);
  useEffect(() => {
    document.body.dataset.ready = 'true';
  }, []);
  return <app.Component key={source} model={app} />;
}

// Grafana core turns same-origin link clicks into history pushes. Without this every link here would be a full page load.
document.addEventListener('click', (event) => {
  const anchor = event.target instanceof Element ? event.target.closest('a') : null;
  const href = anchor?.getAttribute('href');
  if (!href || anchor?.target || event.defaultPrevented || event.ctrlKey || event.metaKey || !href.startsWith('/')) {
    return;
  }
  event.preventDefault();
  locationService.push(href);
});

if (window.location.pathname !== HOME_URL) {
  locationService.replace(`${HOME_URL}${window.location.search}`);
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeContext.Provider value={config.theme2}>
      <GlobalStyles />
      <LocationServiceProvider service={locationService}>
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, height: 48, background: '#181b1f', zIndex: 1000, borderBottom: '1px solid #2c3235', display: 'flex', alignItems: 'center', padding: '0 16px', fontSize: 12 }}>Stand-in for Grafana chrome</div>
        <div style={{ paddingTop: 48 }}>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column' }}>
            <ServiceRouter>
              <Routes>
                <Route path="/a/homelab-observability-app/*" element={<Home />} />
              </Routes>
            </ServiceRouter>
          </div>
        </div>
      </LocationServiceProvider>
    </ThemeContext.Provider>
  </StrictMode>
);
