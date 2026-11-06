import { instant, scalar, seriesOf, type DataLike, type FrameLike } from './semantics';
import type { Wan } from './home';

export interface Path extends Wan {
  title: string;
  port: string;
  availability: number | null;
  down: number | null;
  up: number | null;
}

export interface Device {
  name: string;
  type: string;
  version: string;
  clients: number | null;
  cpu: number | null;
  mem: number | null;
  temp: number | null;
  uptime: number | null;
}

export interface Radio {
  radio: string;
  channel: number | null;
  busy: number | null;
  clients: number | null;
}

export interface NetworkModel {
  paths: Path[];
  devices: Device[];
  radios: Radio[];
  tmobile: { registered: number | null; bars: number | null; rsrp: number | null; sinr: number | null; uptime: number | null };
  dish: { ping: number | null; drop: number | null; obstructed: number | null; uptime: number | null };
}

const byLabel = (data: DataLike, ref: string, label: string) => new Map(seriesOf(data, ref).map((s) => [s.labels[label], instant(s)]));

export function buildNetwork(series: readonly FrameLike[], wans: Wan[]): NetworkModel {
  const data: DataLike = { series: [...series] };
  const one = (ref: string) => scalar(data, ref);
  const metric = (name: string, label: string) => byLabel(data, `I_net_${name}`, label);

  const [availability, down, up] = ['availability', 'linkDown', 'linkUp'].map((name) => metric(name, 'wan'));
  const paths = wans.map((wan) => {
    const [, title, port] = /^(.*) \((.*)\)$/.exec(wan.name) ?? [wan.name, wan.name, ''];
    return { ...wan, title, port, availability: availability.get(wan.name) ?? null, down: down.get(wan.name) ?? null, up: up.get(wan.name) ?? null };
  });

  const [clients, cpu, mem, temp, uptime] = ['deviceClients', 'deviceCpu', 'deviceMem', 'deviceTemp', 'deviceUptime'].map((name) => metric(name, 'name'));
  const devices = seriesOf(data, 'I_net_deviceInfo')
    .map(({ labels: { name, type, version } }) => ({
      name, type, version,
      clients: clients.get(name) ?? null, cpu: cpu.get(name) ?? null, mem: mem.get(name) ?? null, temp: temp.get(name) ?? null, uptime: uptime.get(name) ?? null,
    }))
    .sort((a, b) => (b.clients ?? -1) - (a.clients ?? -1) || a.name.localeCompare(b.name));

  const [channel, busy, radioClients] = ['radioChannel', 'radioBusy', 'radioClients'].map((name) => metric(name, 'ap_radio'));
  const radios = [...new Set([...channel.keys(), ...busy.keys(), ...radioClients.keys()])]
    .map((radio) => ({ radio, channel: channel.get(radio) ?? null, busy: busy.get(radio) ?? null, clients: radioClients.get(radio) ?? null }))
    .sort((a, b) => (b.busy ?? -1) - (a.busy ?? -1));

  return {
    paths,
    devices,
    radios,
    tmobile: { registered: one('I_net_tmobileRegistered'), bars: one('I_net_tmobileBars'), rsrp: one('I_net_tmobileRsrp'), sinr: one('I_net_tmobileSinr'), uptime: one('I_net_tmobileUptime') },
    dish: { ping: one('I_net_dishPing'), drop: one('I_net_dishDrop'), obstructed: one('I_net_dishObstructed'), uptime: one('I_net_dishUptime') },
  };
}
