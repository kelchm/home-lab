const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const num = (v: number | null | undefined, decimals = 0) =>
  v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

export const pct = (v: number | null | undefined) => (v == null ? '–' : `${num(v, 1)} %`);

export const dur = (ms: number) =>
  ms >= HOUR ? `${Math.floor(ms / HOUR)} h ${Math.round((ms % HOUR) / 60_000)} min` : `${Math.max(1, Math.round(ms / 60_000))} min`;

export const days = (s: number | null | undefined) => (s == null ? '–' : s >= 86_400 ? `${num(s / 86_400)} d` : s >= 3600 ? `${num(s / 3600)} h` : `${num(s / 60)} min`);

export const spanLabel = (ms: number) =>
  ms >= 2 * DAY ? `${Math.round(ms / DAY)} d` : ms >= HOUR ? `${num(ms / HOUR, ms % HOUR ? 1 : 0)} h` : `${Math.max(1, Math.round(ms / 60_000))} min`;

export interface Clock {
  hm: (t: number) => string;
  md: (t: number) => string;
  stamp: (t: number) => string;
}

const clocks = new Map<string, Clock>();

export function clockFor(timeZone: string): Clock {
  let clock = clocks.get(timeZone);
  if (!clock) {
    const hm = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    const md = new Intl.DateTimeFormat('en-US', { timeZone, month: 'short', day: 'numeric' });
    const stamp = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
    clock = { hm: (t) => hm.format(t), md: (t) => md.format(t), stamp: (t) => stamp.format(t) };
    clocks.set(timeZone, clock);
  }
  return clock;
}

const MINUTE_STEPS = [5, 10, 15, 30, 60];
const MAX_MINUTE_TICKS = 8;

/** Whole local hours for a day or so, so a window crossing a clock change still labels the hours people read. */
export function timeTicks(from: number, to: number, width: number, clock: Clock): Array<[number, string]> {
  const span = to - from;
  const out: Array<[number, string]> = [];
  if (span > 3 * DAY) {
    const every = Math.ceil(span / DAY / Math.max(2, Math.floor(width / 90)));
    let day = 0;
    for (let t = Math.ceil(from / HOUR) * HOUR; t <= to; t += HOUR) {
      if (clock.hm(t).startsWith('00') && day++ % every === 0) {
        out.push([t, clock.md(t)]);
      }
    }
  } else if (span > 6 * HOUR) {
    const every = width < 420 ? 8 : width < 760 ? 6 : 3;
    const seen = new Set<string>();
    for (let t = Math.ceil(from / HOUR) * HOUR; t <= to; t += HOUR) {
      const label = clock.hm(t);
      if (Number(label.slice(0, 2)) % every === 0 && !seen.has(label)) {
        seen.add(label);
        out.push([t, label]);
      }
    }
  } else {
    const minutes = MINUTE_STEPS.find((m) => span / (m * 60_000) <= MAX_MINUTE_TICKS) ?? 60;
    for (let t = Math.ceil(from / (minutes * 60_000)) * minutes * 60_000; t <= to; t += minutes * 60_000) {
      out.push([t, clock.hm(t)]);
    }
  }
  return out;
}

export const niceMax = (v: number) => {
  if (!(v > 0)) {
    return 1;
  }
  const e = 10 ** Math.floor(Math.log10(v));
  const n = v / e;
  return (n <= 1 ? 1 : n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 6 ? 6 : n <= 8 ? 8 : 10) * e;
};
