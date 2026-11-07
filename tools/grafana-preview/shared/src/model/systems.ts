export type SystemId = 'overview' | 'k8s' | 'pve' | 'spark' | 'nas' | 'net' | 'power' | 'dev';
export type Accent = 'azure' | 'sea' | 'orchid';
export type IconName = 'home' | 'k8s' | 'pve' | 'spark' | 'nas' | 'net' | 'power' | 'dev' | 'back' | 'out';

export interface System {
  id: SystemId;
  /** The display name: the `links` key, and the nav item's `data-system`. */
  name: string;
  tag?: string;
  icon: IconName;
  accent: Accent;
  /** Legacy native-comparison fallback. The Scenes app supplies internal links for every system. */
  inComparison: boolean;
  /** Existing production dashboards, primary first. */
  deeper: Array<[string, string]>;
}

const DRIVES: [string, string] = ['Drive health', 'f8f249a0-be78-41b1-97fe-8d0a92a71b93'];
const HOSTS: [string, string] = ['Hosts', 'hosts'];
const PROBES: [string, string] = ['Probes', 'pi-blackbox'];
const SYNOLOGY: [string, string] = ['Synology / Storage and hardware health', 'synology-health'];

export const SYSTEMS: System[] = [
  { id: 'overview', name: 'Overview', icon: 'home', accent: 'azure', inComparison: true, deeper: [] },
  {
    id: 'k8s', name: 'Kubernetes', tag: 'k8s-prod', icon: 'k8s', accent: 'azure', inComparison: false,
    deeper: [['Kubernetes / Views / Global', 'k8s_views_global'], ['Longhorn / Operations', 'longhorn-operations'], DRIVES, ['cert-manager', 'cdhrcds8aosg0c'], ['Traefik', 'n5bu_kv4k'], ['CloudNativePG', 'cloudnative-pg'], ['Identity', 'pan9z68'], PROBES],
  },
  { id: 'pve', name: 'Proxmox', tag: 'pve-sbx', icon: 'pve', accent: 'sea', inComparison: false, deeper: [['Proxmox via Prometheus', 'Dp7Cd57Zza'], HOSTS, DRIVES] },
  { id: 'spark', name: 'Sparks', tag: 'spark-1 + spark-2', icon: 'spark', accent: 'orchid', inComparison: false, deeper: [['Sparks / GPU and host health', 'sparks-health'], ['AI / Inference serving', 'spark-tensorfold-performance'], HOSTS, DRIVES] },
  { id: 'nas', name: 'Athena', tag: 'Synology NAS', icon: 'nas', accent: 'azure', inComparison: false, deeper: [SYNOLOGY, DRIVES, HOSTS, PROBES] },
  { id: 'net', name: 'Network', tag: 'UniFi + two WANs', icon: 'net', accent: 'sea', inComparison: true, deeper: [['UniFi sites', '9WaGWZaZk'], ['Switches', 'FsfxpWaZz'], ['Access points', 'g5wFWqxZk'], ['Gateway', '4Yo8IZ-Wk']] },
  { id: 'power', name: 'Power', tag: 'three UPS units', icon: 'power', accent: 'orchid', inComparison: false, deeper: [SYNOLOGY] },
  { id: 'dev', name: 'Devices', tag: 'Pis + recovery console', icon: 'dev', accent: 'azure', inComparison: false, deeper: [HOSTS, ['NTP', 'ntp'], ['GLKVM / Recovery console', 'glkvm-console'], PROBES] },
];

export const SYSTEM: Record<SystemId, System> = Object.fromEntries(SYSTEMS.map((system) => [system.id, system])) as Record<SystemId, System>;

export const GRAFANA = 'https://grafana.home.kelch.io';
