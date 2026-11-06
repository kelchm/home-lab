// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import type { DataFrame, PanelData, TimeRange } from '@grafana/data';
import { DomainView, type DomainEvidence, type DomainViewProps } from './DomainView';
import type { DomainContext, DomainDefinition } from './contract';
import { value } from './data';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it('publishes the page context and native chart snapshot with their actual resolver state', () => {
  const timeRange = { from: { valueOf: () => 0 }, to: { valueOf: () => 60000 }, raw: { from: 0, to: 60000 } } as unknown as TimeRange;
  const frame = (refId: string, n: number) => ({ refId, fields: [{ type: 'time', values: [60000] }, { type: 'number', values: [n] }] }) as DataFrame;
  const snapshots: DomainEvidence[] = [];
  let renderedContext: DomainContext | undefined;
  let chartData: PanelData | undefined;
  const domain: DomainDefinition = {
    id: 'power', focus: 'Power', title: 'Power', accent: 'orchid',
    queries: ['required', 'optional'].map((refId) => ({ refId, key: refId, source: 'test', expr: refId, instant: true, range: false, scope: 'test' })),
    pages: [{ id: 'test', title: 'Test', question: 'What was observed?', required: ['required'], sections: (ctx) => {
      renderedContext = ctx;
      return [{ kind: 'chart', id: 'history', title: 'History', refs: ['required', 'optional'], unit: 'short' }];
    } }],
  };
  const props: DomainViewProps = {
    domain, page: domain.pages[0], timeRange, mode: 'live', entity: null, timeZone: 'UTC', href: () => '/', deeper: () => '/',
    data: { state: 'Done', series: [frame('required', 0), frame('optional', 2)], timeRange, request: { targets: domain.queries, range: timeRange } } as unknown as PanelData,
    renderChart: ({ data }) => { chartData = data; return null; }, onEvidence: (evidence) => snapshots.push(evidence),
  };
  const host = document.body.appendChild(document.createElement('div'));
  const root = createRoot(host);
  try {
    act(() => root.render(<DomainView {...props} />));
    const current = snapshots.at(-1)!;
    expect(current.context).toBe(renderedContext);
    expect(current.data).toBe(chartData);
    expect(current.data.series).toBe(current.resolved.series);
    expect(current.resolved.refs.get('required')?.state).toBe('current');
    expect(value(current.context.series, 'required')).toBe(0);

    const failed = { ...props.data, state: 'Error', series: [], errors: [{ refId: 'required', message: 'failed' }, { refId: 'optional', message: 'failed' }] } as unknown as PanelData;
    act(() => root.render(<DomainView {...props} data={failed} />));
    const retained = snapshots.at(-1)!;
    expect(retained.context).toBe(renderedContext);
    expect(retained.data).toBe(chartData);
    expect(retained.resolved.refs.get('required')?.state).toBe('retained');
    expect(retained.context.coverage.retained).toBe(1);
    expect(value(retained.context.series, 'required')).toBe(0);
    expect(value(retained.context.series, 'optional')).toBeNull();
    expect(retained.data.series).toBe(retained.resolved.series);
    expect(retained.data.series).toHaveLength(2);

    const count = snapshots.length;
    act(() => root.render(<DomainView {...props} data={failed} onEvidence={undefined} />));
    expect(snapshots).toHaveLength(count);
  } finally {
    act(() => root.unmount());
    host.remove();
  }
});
