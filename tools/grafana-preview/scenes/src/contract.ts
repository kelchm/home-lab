// Runtime definitions for the persistent Home briefing; domain groups come from the shared registry. No samples are bundled.
import queries from '../../runtime/queries.json';
import { DOMAIN_SOURCE_GROUPS } from '@shared/domains/registry';
export { FOCUSES, type Focus } from '@shared/types';

export interface QueryDef {
  refId: string;
  key: string;
  source: string;
  expr: string;
  instant: boolean;
  range: boolean;
  scope: string;
}

export const COMMON_QUERIES: QueryDef[] = queries.common;
export const NETWORK_QUERIES: QueryDef[] = queries.network;

export const APP_ID = 'homelab-observability-app';
export const APP_ROOT = `/a/${APP_ID}`;
export const HOME_URL = `${APP_ROOT}/explore`;
export const HOME_ALIAS = `${APP_ROOT}/home`;

export const SOURCE_GROUPS = [
  { id: 'common', title: 'Home query source', queries: COMMON_QUERIES },
  ...DOMAIN_SOURCE_GROUPS,
];

export const NATIVE_DASHBOARD_UID = 'home-compare-native';
export const TIME_ZONE = 'America/New_York';
export const MAX_DATA_POINTS = 289;
export const MIN_INTERVAL = '5m';
export const REFRESH_INTERVALS = ['30s', '1m', '5m', '15m'];

export type Source = 'replay' | 'live';

export const DATASOURCES: Record<Source, { uid: string; type: 'prometheus' }> = {
  replay: { uid: 'comparison', type: 'prometheus' },
  live: { uid: 'comparison-live', type: 'prometheus' },
};

export const SOURCE_LABELS: Record<Source, string> = {
  replay: 'Recorded replay',
  live: 'Live',
};

const replayWindow = {
  from: new Date(queries.window.from).toISOString(),
  to: new Date(queries.window.to).toISOString(),
};

export const DEFAULT_RANGE: Record<Source, { from: string; to: string }> = {
  replay: replayWindow,
  live: { from: 'now-24h', to: 'now' },
};
