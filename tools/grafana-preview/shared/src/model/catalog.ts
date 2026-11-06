import type { View } from '../types';

// Ref names only. Expressions live in runtime/queries.json and every sample arrives as a frame, so nothing recorded is bundled here.
const prefixed = (prefix: string, refs: string) => refs.split(' ').map((ref) => prefix + ref);

const HOME = prefixed('H_', 'A P PH PD PC WKA WKB WKC WKF WKG WKN WKI WKX BKA BKB BKC BKD BKE BKF BKG BKJ BKI BKO BKH BKV WNA WNB WNG WNH MV MA MD ME MN MH MU MT AL AT AH');
const MACHINES = prefixed('M_', 'A B C D E F');
const LANES = prefixed('R_', 'k8s_requests spark_tokens nas_read ups_load net_wanRx');
const CHECKS = prefixed('H_', 'P PH PD PC');
const WAN = prefixed('H_', 'WNA WNB WNG WNH');
const MONITOR = prefixed('H_', 'MV MA MD ME MN MH');
const PATHS = prefixed('I_net_', 'availability linkDown linkUp dishPing dishDrop dishObstructed dishUptime tmobileRegistered tmobileBars tmobileRsrp tmobileSinr tmobileUptime');
const INVENTORY = prefixed('I_net_', 'deviceInfo deviceClients deviceCpu deviceMem deviceTemp deviceUptime radioChannel radioBusy radioClients');

/** Read when present so a lane matches the accepted page, but the catalog owes nothing here. */
const UPGRADES = prefixed('R_', 'nas_written net_wanTx ups_runtimeMin');

const COMMON = [...HOME, ...MACHINES, ...LANES, ...UPGRADES];
const ALL = [...COMMON, ...PATHS, ...INVENTORY];

/** Refs a surface resolves and retains. */
export const QUERIES: Record<View, readonly string[]> = {
  navigation: COMMON,
  briefing: COMMON,
  activity: COMMON,
  review: COMMON,
  machines: COMMON,
  checks: COMMON,
  'network-summary': ALL,
  'network-inventory': ALL,
};

/** Refs a surface's own claims rest on. A failure elsewhere doesn't mark it stale. */
export const NEEDS: Record<View, readonly string[]> = {
  navigation: HOME,
  briefing: HOME,
  review: HOME,
  activity: [...HOME, ...LANES, 'M_E'],
  machines: [...MACHINES, ...MONITOR],
  checks: CHECKS,
  'network-summary': [...WAN, ...PATHS],
  'network-inventory': INVENTORY,
};

export const REVIEW_REFS: readonly string[] = HOME;

/** The catalog's naming, so an error on a query no surface reads can be told from one that could be any query. */
export const isCatalogRef = (ref: string) => /^(?:[HMRI]|D_[a-z0-9]+)_/.test(ref);

/** A successful query may answer with no series, and some paths drop that empty frame, so absence alone is no gap here. */
export const MAY_BE_ABSENT: ReadonlySet<string> = new Set([...prefixed('H_', 'WKN WKX BKV'), ...UPGRADES]);
