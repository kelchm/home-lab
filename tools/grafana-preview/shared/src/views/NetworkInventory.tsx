import type { CSSProperties } from 'react';
import { days, num } from '../format';
import type { RefStatus } from '../lifecycle';
import { SYSTEM } from '../model/systems';
import { Bar, Card, Fit, Icon, Status } from '../ui/parts';
import { deepHref, type ViewContext } from './context';

export function NetworkInventory({ ctx }: { ctx: ViewContext }) {
  const { network, constrained, tag, coverage, clock, timeRange } = ctx;
  const devices = network?.devices ?? [];
  const radios = network?.radios ?? [];
  const count = (refs: string[], value: number, label: string) => {
    const states = refs.map((ref) => ctx.resolved.refs.get(ref));
    const known = states.every((state) => state && (['current', 'empty'].includes(state.state) || state.at != null));
    if (!known) return `${label === 'devices' ? 'Device' : 'Radio'} count unknown`;
    const retained = states.filter((state): state is RefStatus => state?.at != null);
    if (!retained.length) return `${value} ${label}`;
    const at = Math.min(...retained.map((state) => state.at!));
    const pending = retained.some((state) => state.state === 'pending');
    return <span title={`Inventory received ${clock.md(at)} ${clock.stamp(at)}`}>{value} {label} · {pending ? 'updating' : 'retained'}</span>;
  };
  const deviceCount = count(['I_net_deviceInfo'], devices.length, 'devices');
  const radioCount = count(['I_net_radioChannel', 'I_net_radioBusy', 'I_net_radioClients'], radios.length, 'radios');
  const system = SYSTEM.net;
  return (
    <Fit constrained={constrained} label="Network inventory">
      <Status coverage={coverage} clock={clock} />
      <div className="g" style={{ '--a': `var(--${system.accent})` } as CSSProperties}>
        <Card eye="UniFi" when="window end" tag={tag} title="Network devices" pill={deviceCount} className="c8">
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Device</th>
                  <th className="r">Clients</th>
                  <th className="r">CPU</th>
                  <th className="r">Mem</th>
                  <th className="r">Temp</th>
                  <th className="r">Up</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => (
                  <tr key={device.name} data-device={device.name} data-row>
                    <td>
                      <b>{device.name}</b>
                      <small className="hide-sm">
                        {device.type} {device.version}
                      </small>
                    </td>
                    <td className="r">{num(device.clients)}</td>
                    <td className="r">{device.cpu == null ? '–' : `${num(device.cpu)} %`}</td>
                    <td className="r">{device.mem == null ? '–' : `${num(device.mem)} %`}</td>
                    <td className="r">{device.temp == null ? '–' : `${num(device.temp)} °C`}</td>
                    <td className="r">{days(device.uptime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card eye="Wi-Fi" when="window end" tag={tag} title="Channel busy" pill={radioCount} className="c4" note="Includes neighbours’ traffic.">
          <div className="bars radios">
            {radios.map((radio) => (
              <Bar
                key={radio.radio}
                label={radio.radio}
                value={radio.busy}
                text={radio.busy == null ? '–' : `${num(radio.busy)} %`}
                small={`ch ${num(radio.channel)} · ${num(radio.clients)} client${radio.clients === 1 ? '' : 's'}`}
                color="sea"
              />
            ))}
          </div>
        </Card>
      </div>
      <div className="deep">
        <strong>Go deeper</strong>
        {system.deeper.map(([name, uid]) => (
          <a key={uid} href={deepHref(uid, timeRange)} target="_blank" rel="noopener">
            {name}
            <Icon name="out" />
          </a>
        ))}
        <em>Opens production Grafana at this window. Log feeds aren’t included here.</em>
      </div>
    </Fit>
  );
}
