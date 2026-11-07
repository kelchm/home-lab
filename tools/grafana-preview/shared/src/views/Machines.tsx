import { useId, type CSSProperties } from 'react';
import { num, pct } from '../format';
import { cpuScaleTop } from '../model/home';
import { HOST_STATES, type HostRow } from '../model/semantics';
import { linePath, runsOf } from '../ui/LanePlot';
import { Card, Fit, Status } from '../ui/parts';
import type { ViewContext } from './context';
import { machineTarget } from './machine-links';

const STATE_COLOR: Record<HostRow['state'], string> = { collecting: 'ok', failing: 'fail', missing: 'warn' };

function Spark({ host, ctx, top, gradient }: { host: HostRow; ctx: ViewContext; top: number; gradient: string }) {
  const { from, to, span } = ctx;
  const values = host.history.flatMap(([, v]) => (v == null ? [] : [v]));
  if (!values.length) {
    return <span className="age">No history</span>;
  }
  const x = (t: number) => (1000 * (t - from)) / (to - from);
  const y = (v: number) => 96 - (92 * v) / top;
  const allRuns = runsOf(host.history);
  const runs = allRuns.filter((run) => run.length > 1);
  const line = runs.map((run) => linePath(run, x, y)).join('');
  const area = runs.map((run) => `${linePath(run, x, y)}L${x(run[run.length - 1][0]).toFixed(1)} 100L${x(run[0][0]).toFixed(1)} 100Z`).join('');
  return (
    <svg className="msp" viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label={`${host.instance} CPU over ${span}: ${pct(Math.min(...values))} to ${pct(Math.max(...values))}`}>
      {allRuns.filter((run) => run.length === 1).map(([[t, v]]) => <circle key={t} cx={x(t)} cy={y(v)} r={2} fill="#4DB0FF" />)}
      <path d={area} fill={`url(#${gradient})`} />
      <path d={line} fill="none" stroke="#4DB0FF" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const Meter = ({ value, color }: { value: number | null; color: string }) =>
  value == null ? (
    <span className="age">No data</span>
  ) : (
    <div className="meter">
      <b>{pct(value)}</b>
      <span className="track">
        <i style={{ '--c': `var(--${color})`, width: `${Math.min(100, value)}%` } as CSSProperties} />
      </span>
    </div>
  );

function Monitoring({ ctx }: { ctx: ViewContext }) {
  const { rulesEvaluating, down, notifyFailures, heartbeat } = ctx.model.monitor;
  const delivery = heartbeat == null ? 'Heartbeat unknown' : heartbeat > 0 ? 'Heartbeat acknowledged' : 'Heartbeat not delivered';
  return (
    <div className="card mon">
      <strong>Monitoring{ctx.atEnd}</strong>
      <span className={rulesEvaluating ? '' : 'unknown'}>{rulesEvaluating ? 'Rules evaluating' : 'Rule coverage unknown'}</span>
      <span>{num(down)} targets failing</span>
      <span>{num(notifyFailures)} notification failures / 24 h</span>
      <span className={heartbeat == null ? 'unknown' : ''}>{delivery}</span>
      {ctx.tag && <span>{ctx.tag}</span>}
    </div>
  );
}

export function Machines({ ctx }: { ctx: ViewContext }) {
  const { model, span, tag, atEnd, constrained, coverage, clock } = ctx;
  const hosts = model.hosts;
  const gradient = `hlc${useId().replace(/\W/g, '')}`;
  const top = cpuScaleTop(hosts);
  const collecting = hosts.filter((host) => host.state === 'collecting').length;
  return (
    <>
      <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
        <defs>
          <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#4DB0FF" stopOpacity={0.4} />
            <stop offset="1" stopColor="#4DB0FF" stopOpacity={0} />
          </linearGradient>
        </defs>
      </svg>
      <Card
        eye="Fleet"
        when={atEnd ? 'Window end' : 'All platforms'}
        tag={tag}
        title="Machines"
        pill={hosts.length ? `${collecting} of ${hosts.length} collecting` : undefined}
        tone={collecting < hosts.length ? 'bad' : ''}
        accent="sea"
        note="Collecting means scrapes are arriving, not that the machine is healthy."
        status={<Status coverage={coverage} clock={clock} />}
      >
        <Fit constrained={constrained} label="Machines">
          {hosts.length > 0 && (
            <table className="mtable">
              <colgroup>
                <col style={{ width: '25%' }} />
                <col style={{ width: '12%' }} />
                <col style={{ width: '18%' }} />
                <col />
                <col style={{ width: '17%' }} />
                <col style={{ width: '9%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Machine</th>
                  <th>Collection</th>
                  <th>CPU</th>
                  <th>
                    CPU over {span}
                    <small className="hide-md">0–{top} %</small>
                  </th>
                  <th>Memory</th>
                  <th style={{ textAlign: 'right' }}>Age</th>
                </tr>
              </thead>
              <tbody>
                {hosts.map((host) => {
                  const target = machineTarget(host, ctx);
                  return (
                  <tr key={`${host.platform}/${host.instance}`} data-host={host.instance} data-state={host.state} data-row>
                    <td>
                      <span className="mname">
                        <a href={target.href} {...(target.outside ? { target: '_blank', rel: 'noopener' } : {})} title={`Open details for ${host.instance}`}>
                          {host.instance}
                        </a>
                        <small>{host.platform}</small>
                      </span>
                    </td>
                    <td className="mo">
                      <span className="state" style={{ '--c': `var(--${STATE_COLOR[host.state]})` } as CSSProperties}>
                        <i />
                        {HOST_STATES[host.state].label}
                      </span>
                    </td>
                    <td className="mc">
                      <Meter value={host.cpu} color="azure" />
                    </td>
                    <td className="ms">
                      <Spark host={host} ctx={ctx} top={top} gradient={gradient} />
                    </td>
                    <td className="mm">
                      <Meter value={host.memory} color="sea" />
                    </td>
                    <td className="age ma">{host.age == null ? '–' : `${num(host.age)} s`}</td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Fit>
      </Card>
      <Monitoring ctx={ctx} />
    </>
  );
}
