import { memo, useId, useMemo, type PointerEvent, type ReactNode } from 'react';
import { niceMax } from '../format';
import type { Lane, Span } from '../model/home';
import type { Point } from '../model/semantics';
import { useSize } from './parts';

const MARGIN = { l: 0, r: 3, t: 6, b: 7 };
const COLOR = { azure: '#4DB0FF', sea: '#3FE6C4', orchid: '#C685FF' };
const MARK = { maint: ['#9FB1C1', 0.6], warn: ['#FFAE1F', 0.8], note: ['#4DB0FF', 0.95] } as const;
/** Past this the label would run outside its own window. */
const LABEL_ROOM = 104;

/** Unbroken runs of samples. A null always ends one, so a line never crosses a gap. */
export function runsOf(points: Point[]) {
  const runs: Array<Array<[number, number]>> = [];
  let run: Array<[number, number]> | null = null;
  for (const [t, v] of points) {
    if (v == null) {
      run = null;
    } else if (run) {
      run.push([t, v]);
    } else {
      runs.push((run = [[t, v]]));
    }
  }
  return runs;
}

export function linePath(run: Array<[number, number]>, x: (t: number) => number, y: (v: number) => number) {
  return run.map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join('');
}

export const visibleSpans = (spans: Span[], from: number, to: number): Span[] =>
  spans.filter(([a, z]) => z > from && a < to && z > a).map(([a, z]) => [Math.max(a, from), Math.min(z, to)]);

export interface LanePlotProps {
  lane: Lane;
  from: number;
  to: number;
  ticks: number[];
  cursor: number | null;
  highlight: Span[];
  emptyText: string;
  alt: string;
  onCursor: (t: number | null) => void;
}

export const LanePlot = memo(function LanePlot({ lane, from, to, ticks, cursor, highlight, emptyText, alt, onCursor }: LanePlotProps) {
  const [ref, { width, height }] = useSize<HTMLDivElement>();
  const gradient = `hlc${useId().replace(/\W/g, '')}`;
  const color = COLOR[lane.color];
  const inner = width - MARGIN.l - MARGIN.r;
  const x = (t: number) => MARGIN.l + (inner * (t - from)) / (to - from);

  const runs = useMemo(() => runsOf(lane.points), [lane.points]);
  const peak = useMemo(() => Math.max(0, ...lane.points.flatMap(([, v]) => (v == null ? [] : [v]))), [lane.points]);
  const top = lane.max != null ? Math.max(lane.max, peak) : niceMax(peak * 1.04);
  const y = (v: number) => MARGIN.t + (height - MARGIN.t - MARGIN.b) * (1 - v / top);

  const paths = useMemo(() => {
    if (!width || !height) {
      return null;
    }
    const base = y(0).toFixed(1);
    return runs.map((run) =>
      run.length > 1 ? (
        <g key={run[0][0]}>
          <path d={`${linePath(run, x, y)}V${base}H${x(run[0][0]).toFixed(1)}Z`} fill={`url(#${gradient})`} />
          <path d={linePath(run, x, y)} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
        </g>
      ) : (
        <circle key={run[0][0]} cx={x(run[0][0])} cy={y(run[0][1])} r={1.6} fill={color} />
      )
    );
    // The scales are derived from exactly these inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, width, height, from, to, top, color, gradient]);

  const onMove = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    onCursor(from + (to - from) * Math.min(1, Math.max(0, (event.clientX - box.left - MARGIN.l) / inner)));
  };

  const strip = (spans: Span[], fill: string, opacity: number, rectY: number, rectH: number): ReactNode =>
    visibleSpans(spans, from, to).map(([a, z]) => <rect key={a} x={x(a).toFixed(1)} y={rectY} width={Math.max(2, x(z) - x(a)).toFixed(1)} height={rectH} fill={fill} fillOpacity={opacity} />);

  const criticalSpans = visibleSpans(lane.marks.crit, from, to);
  const critical = criticalSpans[0];
  const room = critical ? x(critical[1]) - x(critical[0]) : 0;
  const atCursor = cursor == null ? null : lane.points.find(([t]) => Math.abs(t - cursor) <= lane.step / 2)?.[1];

  return (
    <div
      ref={ref}
      className="plot lp"
      onPointerMove={onMove}
      onPointerDown={onMove}
      onPointerLeave={(event) => {
        if (event.pointerType === 'mouse') {
          onCursor(null);
        }
      }}
    >
      {!runs.length ? (
        <div className="empty">{emptyText}</div>
      ) : width > 0 && height > 0 ? (
        <svg role="img" aria-label={alt}>
          <defs>
            <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity={0.38} />
              <stop offset="1" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          {ticks.map((t) => (
            <line key={t} x1={x(t).toFixed(1)} x2={x(t).toFixed(1)} y1={0} y2={height - MARGIN.b} stroke="rgba(150,205,245,.06)" />
          ))}
          {criticalSpans.map(([a, z]) => (
            <rect key={a} x={Math.max(MARGIN.l, x(a)).toFixed(1)} y={0} width={Math.max(2, Math.min(width - MARGIN.r, x(z)) - Math.max(MARGIN.l, x(a))).toFixed(1)} height={height - MARGIN.b} fill="rgba(255,75,110,.17)" />
          ))}
          {paths}
          {strip(lane.marks.maint, MARK.maint[0], MARK.maint[1], height - 5, 3)}
          {strip(lane.marks.warn, MARK.warn[0], MARK.warn[1], height - 5, 3)}
          {strip(lane.marks.note, MARK.note[0], MARK.note[1], height - 5, 3)}
          {strip(lane.marks.crit, '#FF4B6E', 1, height - 6, 4)}
          {lane.label && room > LABEL_ROOM && (
            <text x={(x(critical[0]) + 6).toFixed(1)} y={14} style={{ font: '500 11px Inter,sans-serif', fill: '#FF8AA1' }}>
              {lane.label}
            </text>
          )}
          {visibleSpans(highlight, from, to).map(([a, z]) => (
            <rect key={a} x={x(a).toFixed(1)} y={0} width={Math.max(2, x(z) - x(a)).toFixed(1)} height={height} fill="rgba(233,241,247,.14)" />
          ))}
          {cursor != null && (
            <g>
              <line x1={x(cursor).toFixed(1)} x2={x(cursor).toFixed(1)} y1={0} y2={height - MARGIN.b} stroke="#E9F1F7" strokeOpacity={0.55} />
              {atCursor != null && <circle cx={x(cursor).toFixed(1)} cy={y(atCursor).toFixed(1)} r={3} fill={color} stroke="#0B151F" strokeWidth={1.5} />}
            </g>
          )}
        </svg>
      ) : null}
    </div>
  );
});
