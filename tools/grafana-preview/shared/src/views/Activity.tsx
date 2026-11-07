import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { num, timeTicks } from '../format';
import type { RefState } from '../lifecycle';
import { sampleAt, valueAtEnd, type Lane } from '../model/home';
import { SYSTEM } from '../model/systems';
import { useHighlight } from '../ui/highlight';
import { LanePlot } from '../ui/LanePlot';
import { Card, Fit, Icon, Status } from '../ui/parts';
import { Rollup, summarize } from './Checks';
import { targetOf, type ViewContext } from './context';

const CPU_SCALE = 50;
const NO_SPANS: never[] = [];

const EMPTY_TEXT: Partial<Record<RefState, string>> = { failed: 'Query failed', unanswered: 'No result returned', pending: 'Loading' };

function LaneRow({ lane, cursor, setCursor, ticks, ctx }: { lane: Lane; cursor: number | null; setCursor: (t: number | null) => void; ticks: number[]; ctx: ViewContext }) {
  const { from, to, clock, resolved } = ctx;
  const system = SYSTEM[lane.system];
  const target = targetOf(system, ctx);
  const highlight = useHighlight();
  const status = resolved.refs.get(lane.ref);
  const state = status?.state ?? 'empty';
  const value = cursor == null ? valueAtEnd(lane, to) : sampleAt(lane, cursor);
  const heading = (
    <>
      <b className="n">
        <Icon name={system.icon} />
        {system.name}
        {target?.outside && <Icon name="out" className="o" />}
      </b>
      <small>{lane.metric}</small>
    </>
  );
  return (
    <div className="lane" data-lane={system.name} data-state={state} data-row style={{ '--l': `var(--${lane.color})` } as CSSProperties}>
      {target ? (
        <a href={target.href} {...(target.outside ? { target: '_blank', rel: 'noopener' } : {})}>
          {heading}
        </a>
      ) : (
        <div className="lh">{heading}</div>
      )}
      <LanePlot
        lane={lane}
        from={from}
        to={to}
        ticks={ticks}
        cursor={cursor}
        highlight={highlight?.system === lane.system ? highlight.spans : NO_SPANS}
        emptyText={EMPTY_TEXT[state] ?? 'No samples in this window'}
        alt={`${system.name}: ${lane.metric}`}
        onCursor={setCursor}
      />
      <div className="lv">
        <b>{lane.points.length ? num(value, lane.decimals) : 'No data'}</b>
        <small>{status?.at != null ? `received ${clock.hm(status.at)}` : lane.unit}</small>
      </div>
    </div>
  );
}

export function Activity({ ctx }: { ctx: ViewContext }) {
  const { model, from, to, clock, span, tag, constrained, coverage } = ctx;
  const [cursor, setCursor] = useState<number | null>(null);
  const move = useCallback((t: number | null) => setCursor(t), []);

  useEffect(() => {
    const clear = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest('.plot')) {
        setCursor(null);
      }
    };
    document.addEventListener('pointerdown', clear);
    return () => document.removeEventListener('pointerdown', clear);
  }, []);

  const ticks = useMemo(() => timeTicks(from, to, 600, clock), [from, to, clock]);
  const gridTimes = useMemo(() => ticks.map(([t]) => t), [ticks]);
  const summary = summarize(model.checks);
  const cpuScaleHolds = model.lanes.filter((lane) => lane.max === CPU_SCALE).every((lane) => lane.points.every(([, v]) => v == null || v <= CPU_SCALE));

  return (
    <Card
      eye="Activity"
      when={span}
      tag={tag}
      title="Across the lab"
      pill={cursor == null ? 'At window end' : clock.hm(cursor)}
      className="map"
      status={<Status coverage={coverage} clock={clock} />}
    >
      <Fit constrained={constrained} label="Across the lab">
        <div className="rows">
          {model.lanes.map((lane) => (
            <LaneRow key={lane.system} lane={lane} cursor={cursor} setCursor={move} ticks={gridTimes} ctx={ctx} />
          ))}
          <div className="axis" aria-hidden="true">
            <div>
              {ticks
                .filter(([t]) => (t - from) / (to - from) > 0.03 && (t - from) / (to - from) < 0.97)
                .map(([t, label]) => (
                  <span key={t} style={{ left: `${((100 * (t - from)) / (to - from)).toFixed(2)}%` }}>
                    {label}
                  </span>
                ))}
            </div>
          </div>
        </div>
      </Fit>
      {/* With natural height the checks view sits directly below and carries this roll-up. In a fixed grid it's a collapsed row, so a failure must stay visible here, outside the scroll. */}
      {constrained && (
        <div className="checks sumrow">
          <Rollup bands={model.rollup} summary={summary} hint={`all ${summary.total} below`} ctx={ctx} />
        </div>
      )}
      <div className="keyline">
        <span>
          <i className="w" />
          Critical window
        </span>
        <span>
          <i style={{ '--c': 'var(--warn)' } as CSSProperties} />
          Warning firing
        </span>
        {model.notices && (
          <span>
            <i style={{ '--c': 'var(--azure)' } as CSSProperties} />
            Under the 10 min notice line
          </span>
        )}
        <span>
          <i style={{ '--c': 'var(--muted)' } as CSSProperties} />
          Maintenance
        </span>
        <span className="end">{`Each lane has its own scale${cpuScaleHolds ? `; CPU lanes use 0–${CPU_SCALE} %` : ''}`}</span>
      </div>
    </Card>
  );
}
