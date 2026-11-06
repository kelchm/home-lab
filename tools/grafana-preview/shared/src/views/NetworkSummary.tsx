import type { CSSProperties, ReactNode } from 'react';
import { days, num } from '../format';
import type { Path } from '../model/network';
import { statusOf } from '../model/status';
import { SYSTEM, type Accent } from '../model/systems';
import { Card, Fit, Icon, Status, Strip } from '../ui/parts';
import type { ViewContext } from './context';

const Stat = ({ value, unit, label }: { value: string; unit?: string; label: string }) => (
  <div>
    <b>
      {value}
      {unit && value !== '–' && <small>{unit}</small>}
    </b>
    <span>{label}</span>
  </div>
);

function pathState(path: Path, atEnd: string) {
  if (path.status === 'online') {
    return { pill: atEnd ? `Online${atEnd}` : 'Reporting online', tone: '' as const };
  }
  return path.status === 'offline' ? { pill: atEnd ? `Offline${atEnd}` : 'Reporting offline', tone: 'bad' as const } : { pill: 'State unknown', tone: 'unknown' as const };
}

function PathCard({ path, accent, facts, ctx }: { path: Path; accent: Accent; facts: ReactNode; ctx: ViewContext }) {
  const { from, to, span, tag, clock } = ctx;
  const { pill, tone } = pathState(path, ctx.atEnd);
  return (
    <Card eye={path.port} when={span} tag={tag} title={path.title} pill={pill} tone={tone} className="c6" accent={accent}>
      <div className="stats" style={{ '--n': 4 } as CSSProperties}>
        <Stat value={path.latency == null ? '–' : num(path.latency * 1000)} unit="ms" label="To its monitors" />
        <Stat value={num(path.availability)} unit="%" label="Monitor availability" />
        <Stat value={path.down == null ? '–' : num(path.down / 1e6)} unit="Mbit/s" label="Provider down" />
        <Stat value={path.up == null ? '–' : num(path.up / 1e6)} unit="Mbit/s" label="Provider up" />
      </div>
      <div style={{ marginTop: 12 }}>
        <Strip bands={path.bands} from={from} to={to} label={`${path.name} state over ${span}`} clock={clock} />
      </div>
      <div className="meta" style={{ marginTop: 9 }}>
        {facts}
      </div>
    </Card>
  );
}

export function NetworkSummary({ ctx }: { ctx: ViewContext }) {
  const { model, network, constrained, links, coverage, clock, trusted, loading, atEnd } = ctx;
  const system = SYSTEM.net;
  const paths = network?.paths ?? [];
  const online = paths.filter((path) => path.status === 'online').length;
  const unknown = paths.filter((path) => path.status === 'unknown').length;
  const devices = network?.devices.length ?? 0;
  const lead = `${online} of ${paths.length} paths ${atEnd ? `online${atEnd}` : 'reporting online'}${unknown ? `, ${unknown} unknown` : ''}`;
  const parts = [lead, devices ? `${devices} UniFi devices` : 'UniFi devices unknown', statusOf(model.review, 'net', trusted, loading).text];
  const tmobile = network?.tmobile;
  const dish = network?.dish;
  const registered = tmobile?.registered == null ? 'registration unknown' : tmobile.registered === 1 ? 'registered' : 'not registered';
  const accents: Record<string, { accent: Accent; facts: ReactNode }> = {
    'T-Mobile': {
      accent: 'azure',
      facts: (
        <>
          5G <b>{num(tmobile?.bars)}</b> bars · RSRP <b>{num(tmobile?.rsrp)} dBm</b> · SINR <b>{num(tmobile?.sinr)} dB</b> · {registered} · gateway up <b>{days(tmobile?.uptime)}</b>
        </>
      ),
    },
    Starlink: {
      accent: 'sea',
      facts: (
        <>
          Dish ping <b>{num(dish?.ping)} ms</b> · dropped <b>{num(dish?.drop, 1)} %</b> · obstructed <b>{num(dish?.obstructed, 1)} %</b> · dish up <b>{days(dish?.uptime)}</b>
        </>
      ),
    },
  };
  return (
    <Fit constrained={constrained} label="Network summary">
      <div className="head" style={{ '--a': `var(--${system.accent})` } as CSSProperties}>
        <span className="ico">
          <Icon name={system.icon} />
        </span>
        <div className="txt">
          <h2>
            {system.name}
            <span>{system.tag}</span>
          </h2>
          <p>{parts.join(' · ')}</p>
        </div>
        {links.Overview && (
          <a className="back" href={links.Overview}>
            <Icon name="back" />
            Overview
          </a>
        )}
      </div>
      <Status coverage={coverage} clock={clock} />
      <div className="g" style={{ '--a': `var(--${system.accent})` } as CSSProperties}>
        {paths.map((path) => (
          <PathCard key={path.name} path={path} ctx={ctx} {...(accents[path.title] ?? { accent: 'azure', facts: null })} />
        ))}
      </div>
    </Fit>
  );
}
