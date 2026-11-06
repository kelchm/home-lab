import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { num, type Clock } from '../format';
import type { Coverage } from '../lifecycle';
import type { Band } from '../model/semantics';
import type { MarkKind } from '../model/status';
import type { Accent, IconName } from '../model/systems';

const ICONS: Record<IconName, ReactNode> = {
  home: <path d="M3.5 10.5 12 3.5l8.5 7V20a.8.8 0 0 1-.8.8H15v-6.3H9v6.3H4.3a.8.8 0 0 1-.8-.8z" />,
  k8s: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="2.6" />
      <path d="M12 3.5v5.9M12 14.6v5.9M3.5 12h5.9M14.6 12h5.9M6 6l4.2 4.2M13.8 13.8 18 18M18 6l-4.2 4.2M10.2 13.8 6 18" />
    </>
  ),
  pve: (
    <>
      <rect x="3.5" y="4" width="17" height="7" rx="2" />
      <rect x="3.5" y="13" width="17" height="7" rx="2" />
      <path d="M7.5 7.5h.01M7.5 16.5h.01" />
    </>
  ),
  spark: <path d="M12 3.2 14.3 9.7 20.8 12l-6.5 2.3L12 20.8l-2.3-6.5L3.2 12l6.5-2.3z" />,
  nas: (
    <>
      <ellipse cx="12" cy="5.8" rx="8" ry="2.8" />
      <path d="M4 5.8v12.4c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8V5.8M4 12c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8" />
    </>
  ),
  net: <path d="M4 8h15M15 4l4 4-4 4M20 16H5M9 12l-4 4 4 4" />,
  power: <path d="M13.3 2.8 5 13.8h6.3l-.7 7.4 8.4-11h-6.3z" />,
  dev: (
    <>
      <rect x="2.8" y="6" width="18.4" height="12" rx="2" />
      <path d="M6.5 10h.01M10.2 10h.01M13.9 10h.01M17.5 10h.01M7.5 14h9" />
    </>
  ),
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  out: <path d="M7 17 17 7M8.5 7H17v8.5" />,
};

export const Icon = ({ name, className = '' }: { name: IconName; className?: string }) => (
  <svg className={`i ${className}`} viewBox="0 0 24 24" aria-hidden="true">
    {ICONS[name]}
  </svg>
);

export const Mark = ({ kind }: { kind: MarkKind }) => (kind ? <i className={`mark ${kind}`} aria-hidden="true" /> : null);

export interface CardProps {
  eye?: string;
  when?: string;
  /** Where these figures came from, so a surface scrolled on its own still says. */
  tag?: string;
  title?: ReactNode;
  pill?: ReactNode;
  tone?: '' | 'bad' | 'unknown';
  className?: string;
  accent?: Accent;
  note?: ReactNode;
  status?: ReactNode;
  children?: ReactNode;
}

export function Card({ eye, when, tag, title, pill, tone = '', className = '', accent, note, status, children }: CardProps) {
  const label = [when, tag].filter(Boolean).join(' · ');
  return (
    <section className={`card ${className}${tone === 'bad' ? ' bad' : ''}`} style={accent ? ({ '--a': `var(--${accent})` } as CSSProperties) : undefined}>
      <header>
        <div>
          {eye && (
            <div className="eye">
              <b>{eye}</b>
              {label ? `  ${label}` : ''}
            </div>
          )}
          {title && <h3>{title}</h3>}
        </div>
        {pill && <span className={`pill ${tone}`}>{pill}</span>}
      </header>
      {note && <p className="note">{note}</p>}
      {status}
      {children}
    </section>
  );
}

export function statusText(coverage: Coverage, clock: Clock) {
  const { freshness, missing, retainedAt, unanswered } = coverage;
  switch (freshness) {
    case 'loading':
      return retainedAt == null ? null : `Updating · showing data received ${clock.stamp(retainedAt)}`;
    case 'failed':
      return 'Query failed · no data loaded';
    case 'unavailable':
      return 'No result returned · can’t tell empty from failed';
    case 'stale':
      return `${unanswered ? 'No result returned' : 'Query failed'} · showing data received ${retainedAt == null ? 'earlier' : clock.stamp(retainedAt)}`;
    case 'partial':
      return `${missing} ${missing === 1 ? 'query' : 'queries'} without a result · affected items are unknown`;
    case 'empty':
      return 'No data in this range';
    default:
      return null;
  }
}

export function Status({ coverage, clock }: { coverage: Coverage; clock: Clock }) {
  const text = statusText(coverage, clock);
  if (coverage.freshness === 'loading' && coverage.retainedAt == null) {
    return <p className="status quiet">Loading</p>;
  }
  return text ? (
    <p className={`status${coverage.freshness === 'empty' ? ' quiet' : ''}`} role="status">
      {text}
    </p>
  ) : null;
}

export function Strip({ bands, from, to, label, clock }: { bands: Band[]; from: number; to: number; label: string; clock: Clock }) {
  const span = to - from;
  const position = (a: number, z: number) => ({ left: `${((100 * (a - from)) / span).toFixed(3)}%`, width: `${((100 * (z - a)) / span).toFixed(3)}%` });
  return (
    <span className="strip" role="img" aria-label={label}>
      {bands.map(([a, z, v]) => (
        <i key={a} className={v === 0 ? 'f' : ''} style={position(a, z)} title={`${clock.hm(a)}–${clock.hm(z)} ${v === 0 ? 'failed' : 'passing'}`} />
      ))}
    </span>
  );
}

export function Bar({ label, value, text, small, color = 'a' }: { label: string; value: number | null; text?: string; small?: string; color?: string }) {
  return (
    <div className="bar" data-row>
      <div>
        <span title={label}>{label}</span>
        <b>
          {text ?? `${num(value, 1)} %`}
          {small && <small> {small}</small>}
        </b>
      </div>
      <span className="track">
        <i style={{ '--c': `var(--${color})`, width: `${Math.min(100, value ?? 0).toFixed(1)}%` } as CSSProperties} />
      </span>
    </div>
  );
}

/** Pure, so what the cue claims can be tested without a layout engine. */
export function overflowOf(metrics: { scrollTop: number; clientHeight: number; scrollHeight: number; rowBottoms: number[] }) {
  const { scrollTop, clientHeight, scrollHeight, rowBottoms } = metrics;
  const bottom = scrollTop + clientHeight;
  const below = scrollHeight - bottom > 2;
  return { above: scrollTop > 2, below, rows: below ? rowBottoms.filter((edge) => edge > bottom + 1).length : 0 };
}

interface Overflow {
  above: boolean;
  below: boolean;
  rows: number;
}

/** A constrained surface scrolls inside itself and says what's hidden. An unconstrained one takes its natural height. */
export function Fit({ constrained, label, children }: { constrained: boolean; label: string; children: ReactNode }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState<Overflow>({ above: false, below: false, rows: 0 });

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) {
      return;
    }
    const top = el.getBoundingClientRect().top - el.scrollTop;
    const rowBottoms = Array.from(el.querySelectorAll('[data-row]'), (row) => row.getBoundingClientRect().bottom - top);
    const next = overflowOf({ scrollTop: el.scrollTop, clientHeight: el.clientHeight, scrollHeight: el.scrollHeight, rowBottoms });
    setOverflow((prev) => (prev.above === next.above && prev.below === next.below && prev.rows === next.rows ? prev : next));
  }, []);

  // Content can change without any observed box resizing, so every commit re-measures. `measure` bails out when nothing moved.
  useEffect(measure);

  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof ResizeObserver === 'undefined') {
      return;
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (el.firstElementChild) {
      observer.observe(el.firstElementChild);
    }
    return () => observer.disconnect();
  }, [measure, constrained]);

  if (!constrained) {
    return <>{children}</>;
  }
  const scrollable = overflow.above || overflow.below;
  return (
    <div className="fit" data-above={overflow.above || undefined} data-below={overflow.below || undefined}>
      <div className="fit-scroll" ref={scroller} onScroll={measure} tabIndex={scrollable ? 0 : undefined} role={scrollable ? 'region' : undefined} aria-label={scrollable ? `${label}, scrollable` : undefined}>
        <div>{children}</div>
      </div>
      {overflow.below && (
        <button type="button" className="fit-cue" onClick={() => scroller.current?.scrollBy({ top: (scroller.current?.clientHeight ?? 0) * 0.8, behavior: 'smooth' })}>
          {overflow.rows ? `${overflow.rows} more below` : 'More below'}
        </button>
      )}
    </div>
  );
}

export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') {
      return;
    }
    const read = () => setSize((prev) => (prev.width === el.clientWidth && prev.height === el.clientHeight ? prev : { width: el.clientWidth, height: el.clientHeight }));
    const observer = new ResizeObserver(read);
    observer.observe(el);
    read();
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}
