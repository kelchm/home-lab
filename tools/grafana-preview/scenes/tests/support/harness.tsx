import { dateTime, type DataQueryRequest, FieldType, LoadingState, type PanelData, toDataFrame } from '@grafana/data';
import { config, LocationServiceProvider, locationService, setChromeHeaderHeightHook, setDataSourceSrv, setPluginImportUtils, setReturnToPreviousHook, setRunRequest } from '@grafana/runtime';
import { render } from '@testing-library/react';
import { act } from 'react';
import type { ComponentType, ReactNode } from 'react';
import { Route, Router, Routes } from 'react-router-dom';
import { Observable, Subject } from 'rxjs';

import { HOME_URL, type Source } from '../../src/contract';
import { useAppLocation } from '../../src/hooks';
import { recordedFrames } from './recorded';
import { buildApp } from '../../src/scene';

export interface RecordedRun {
  uid?: string;
  refIds: string[];
  from: string;
  to: string;
  maxDataPoints?: number;
  cancelled: boolean;
  respond: (data?: Partial<PanelData>) => void;
}

export const runs: RecordedRun[] = [];
let autoRespond = true;
let recorded = false;

export const holdResponses = () => {
  autoRespond = false;
};

/** Answers from root's catalog, which the real model needs. The default frames are enough for tests that stub the renderer. */
export const useRecordedData = () => {
  recorded = true;
};

const answer = (request: DataQueryRequest): PanelData => ({
  state: LoadingState.Done,
  series: recorded ? recordedFrames(request) : request.targets.map((target) =>
    toDataFrame({ refId: target.refId, fields: [{ name: 'Time', type: FieldType.time, values: [1793496424636] }, { name: 'Value', type: FieldType.number, values: [1], labels: { wan: 'a' } }] })
  ),
  request,
  timeRange: request.range,
});

const fakeDatasource = (uid: string) => ({ uid, type: 'prometheus', getRef: () => ({ uid, type: 'prometheus' }), interpolateVariablesInQueries: undefined, query: () => new Observable() });

/** A real SceneApp with real URL sync. Only the network boundary and the panel plugin loader are fake. */
export function installFakes() {
  runs.length = 0;
  autoRespond = true;
  recorded = false;
  config.exploreEnabled = true;
  setRunRequest((_datasource, request) => {
    const subject = new Subject<PanelData>();
    const uid = request.targets[0]?.datasource?.uid;
    const run: RecordedRun = {
      uid,
      refIds: request.targets.map((target) => target.refId),
      from: request.range.from.toISOString(),
      to: request.range.to.toISOString(),
      maxDataPoints: request.maxDataPoints,
      cancelled: false,
      respond: (data = {}) => subject.next({ ...answer(request), ...data }),
    };
    runs.push(run);
    return new Observable<PanelData>((subscriber) => {
      const inner = subject.subscribe(subscriber);
      if (autoRespond) {
        queueMicrotask(() => run.respond());
      }
      return () => {
        run.cancelled = true;
        inner.unsubscribe();
      };
    });
  });
  setDataSourceSrv({
    get: async (ref: string | { uid?: string }) => fakeDatasource(typeof ref === 'string' ? ref : (ref?.uid ?? 'comparison')),
    getInstanceSettings: (ref: string | { uid?: string }) => {
      const uid = typeof ref === 'string' ? ref : (ref?.uid ?? 'comparison');
      return { uid, name: uid, type: 'prometheus', meta: { id: 'prometheus' } };
    },
    getList: () => [],
    reload: async () => {},
    registerRuntimeDataSource: () => {},
  } as never);
}

setChromeHeaderHeightHook(() => 48);
setReturnToPreviousHook(() => () => {});
setPluginImportUtils({
  importPanelPlugin: () => new Promise(() => {}),
  getPanelPluginFromCache: () => undefined,
} as never);

export const settle = () => act(async () => {
  await new Promise((resolve) => setTimeout(resolve, 20));
});

export const navigate = (search: string) => act(async () => {
  locationService.push(`${HOME_URL}${search}`);
  await new Promise((resolve) => setTimeout(resolve, 20));
});

// locationService runs on memory history under test, so window.history would not move it.
const history = () => locationService.getHistory() as unknown as { goBack(): void; goForward(): void; length: number };

export const historyLength = () => history().length;

export const back = () => act(async () => {
  history().goBack();
  await new Promise((resolve) => setTimeout(resolve, 20));
});

export const forward = () => act(async () => {
  history().goForward();
  await new Promise((resolve) => setTimeout(resolve, 20));
});

/** Routes from locationService, as Grafana core does, so that pushes and popstate reach Scenes' URL sync. */
function ServiceRouter({ children }: { children: ReactNode }) {
  const location = useAppLocation();
  return (
    <Router location={location} navigator={locationService.getHistory() as never}>
      {children}
    </Router>
  );
}

export async function mountAt(search: string, source: Source = 'replay') {
  locationService.push(`${HOME_URL}${search}`);
  const app = buildApp(source);
  const view = render(
    <LocationServiceProvider service={locationService}>
      <ServiceRouter>
        <Routes>
          <Route path="/a/homelab-observability-app/*" element={<app.Component model={app} />} />
        </Routes>
      </ServiceRouter>
    </LocationServiceProvider>
  );
  await settle();
  return { app, view };
}

/** Mounts the real plugin root, so source changes and the bare app URL go through the same path as production. */
export async function mountRoot(search: string) {
  locationService.push(`${HOME_URL}${search}`);
  const { plugin } = await import('../../src/module');
  const Root = plugin.root as unknown as ComponentType;
  const view = render(
    <LocationServiceProvider service={locationService}>
      <ServiceRouter>
        <Routes>
          <Route path="/a/homelab-observability-app/*" element={<Root />} />
        </Routes>
      </ServiceRouter>
    </LocationServiceProvider>
  );
  await settle();
  return view;
}

export const setRole = (orgRole: 'Viewer' | 'Admin') => {
  const canWrite = orgRole === 'Admin';
  config.bootData = { user: { orgRole, permissions: { 'datasources:explore': canWrite, 'dashboards:write': canWrite } } } as never;
};

export const searchParams = () => new URLSearchParams(locationService.getLocation().search);

export const range = { from: dateTime(0), to: dateTime(1000), raw: { from: 'now-1h', to: 'now' } };
