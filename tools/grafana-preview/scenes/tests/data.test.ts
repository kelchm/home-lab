import { applyFieldOverrides, createTheme, dateTime, type DataFrame, LoadingState, type PanelData, toDataFrame } from '@grafana/data';
import { DataProviderProxy, type SceneDataProvider, SceneDataNode } from '@grafana/scenes';
import { firstValueFrom } from 'rxjs';
import { describe, expect, it } from 'vitest';

import { mergeData, scopeData, SharedFrames } from '../src/data';

const frame = (refId: string): DataFrame => toDataFrame({ refId, fields: [{ name: 'Value', values: [1] }] });
const timeRange = { from: dateTime(0), to: dateTime(1000), raw: { from: 'now-1h', to: 'now' } };
const panelData = (refIds: string[], extra: Partial<PanelData> = {}): PanelData => ({
  state: LoadingState.Done,
  series: refIds.map(frame),
  timeRange,
  structureRev: 1,
  request: { targets: refIds.map((refId) => ({ refId })) } as PanelData['request'],
  ...extra,
});

describe('mergeData', () => {
  it('keeps common frames while Network has not answered yet', () => {
    const common = panelData(['H_A']);
    expect(mergeData([common, undefined])).toBe(common);
  });

  it('concatenates frames and request targets', () => {
    const merged = mergeData([panelData(['H_A']), panelData(['I_net'])])!;
    expect(merged.series.map((item) => item.refId)).toEqual(['H_A', 'I_net']);
    expect(merged.request!.targets.map((item) => item.refId)).toEqual(['H_A', 'I_net']);
    expect(merged.structureRev).toBe(2);
  });

  it('reports in-progress work over a finished failure, and a failure over success', () => {
    const failed = panelData(['H_A'], { state: LoadingState.Error, errors: [{ message: 'boom', refId: 'H_A' }] });
    expect(mergeData([failed, panelData(['I'], { state: LoadingState.Loading })])!.state).toBe(LoadingState.Loading);
    const merged = mergeData([failed, panelData(['I'])])!;
    expect(merged.state).toBe(LoadingState.Error);
    expect(merged.errors).toHaveLength(1);
  });
});

describe('scopeData', () => {
  it('narrows frames, targets and errors together', () => {
    const data = panelData(['A', 'B'], { state: LoadingState.Error, errors: [{ message: 'a', refId: 'A' }, { message: 'b', refId: 'B' }] });
    const scoped = scopeData(data, new Set(['A']));
    expect(scoped.series).toHaveLength(1);
    expect(scoped.request!.targets).toHaveLength(1);
    expect(scoped.errors!.map((error) => error.message)).toEqual(['a']);
  });

  it('does not fail a chart for another query, and keeps errors with no refId', () => {
    const other = scopeData(panelData(['A', 'B'], { state: LoadingState.Error, errors: [{ message: 'b', refId: 'B' }] }), new Set(['A']));
    expect(other.state).toBe(LoadingState.Done);
    expect(other.errors).toBeUndefined();
    const general = scopeData(panelData(['A'], { state: LoadingState.Error, errors: [{ message: 'all' }] }), new Set(['A']));
    expect(general.state).toBe(LoadingState.Error);
    expect(general.errors).toHaveLength(1);
  });

  it('leaves unscoped data untouched', () => {
    const data = panelData(['A']);
    expect(scopeData(data)).toBe(data);
  });
});

describe('SharedFrames', () => {
  const finished = () => new SceneDataNode({ data: panelData(['H_A', 'R_net_wanRx']) });
  const late = () => new SceneDataNode();

  it('shows frames to a consumer that activates after its source finished', () => {
    const source = late();
    const consumer = new SharedFrames({ sources: [source.getRef() as never] });
    source.setState({ data: panelData(['H_A']) });
    consumer.activate();
    expect(consumer.state.data?.series).toHaveLength(1);
  });

  it('exists because DataProviderProxy misses that case', () => {
    const source = late();
    const proxy = new DataProviderProxy({ source: source.getRef() as never });
    source.setState({ data: panelData(['H_A']) });
    proxy.activate();
    expect(proxy.state.data?.series ?? []).toHaveLength(0);
  });

  it('follows later results and does not touch its sources', () => {
    const source = finished();
    const consumer = new SharedFrames({ sources: [source.getRef() as never], refIds: new Set(['R_net_wanRx']) });
    consumer.activate();
    expect(consumer.state.data?.series.map((item) => item.refId)).toEqual(['R_net_wanRx']);
    source.setState({ data: panelData(['H_A']) });
    expect(consumer.state.data?.series).toEqual([]);
  });

  it('stops listening when it deactivates', () => {
    const source = finished();
    const consumer = new SharedFrames({ sources: [source.getRef() as never] });
    const deactivate = consumer.activate();
    deactivate();
    const before = consumer.state.data;
    source.setState({ data: panelData(['H_A']) });
    expect(consumer.state.data).toBe(before);
  });

  it('publishes scoped data through the results stream', async () => {
    const source = finished();
    const stream = source as unknown as SceneDataProvider;
    const consumer = new SharedFrames({ sources: [stream.getRef()], refIds: new Set(['H_A']) });
    const result = firstValueFrom(consumer.getResultsStream());
    source.setState({ data: panelData(['H_A', 'B']) });
    expect((await result).origin).toBe(consumer);
  });
});

describe('resource chart scope', () => {
  it('keeps each Spark the same hue across hardware measures', async () => {
    const { chartData } = await import('../src/domain-scene');
    const frame = (refId: string, instance: string) => toDataFrame({ refId, fields: [
      { name: 'Time', type: 'time', values: [1000] },
      { name: 'Value', type: 'number', values: [7], labels: { instance } },
    ] });
    const data = panelData([], { series: [frame('D_spark_memory', 'spark-1'), frame('D_spark_temperature', 'spark-1'), frame('D_spark_memory', 'spark-2')] });
    const memory = chartData(data, ['D_spark_memory'], null);
    const temperature = chartData(data, ['D_spark_temperature'], null);
    expect(memory.series[0].fields[1].config.color).toEqual(temperature.series[0].fields[1].config.color);
    expect(memory.series[0].fields[1].config.color).not.toEqual(memory.series[1].fields[1].config.color);
    expect(memory.series[0].fields[1].config.displayName).toBe('spark-1');
    expect(memory.series[1].fields[1].config.displayName).toBe('spark-2');
  });

  it('keeps native series colors stable across ordering, label order, entity drill-in and refresh values', async () => {
    const { chartData, SERIES_COLORS } = await import('../src/domain-scene');
    const labelled = (labels: Record<string, string>, value = 7) => toDataFrame({ refId: 'D_net_receive', fields: [
      { name: 'Time', type: 'time', values: [1000] },
      { name: 'Value', type: 'number', values: [value], labels },
    ] });
    const a = labelled({ device: 'switch-a', port: '1' });
    const b = labelled({ device: 'switch-b', port: '2' });
    const data = panelData(['D_net_receive'], { series: [a, b] });
    const overview = chartData(data, ['D_net_receive'], null);
    const color = (frame: DataFrame) => frame.fields[1].config.color;
    const expected = color(overview.series[0]);
    expect(expected?.mode).toBe('fixed');
    expect(SERIES_COLORS).toContain(expected?.fixedColor);
    expect(color(chartData({ ...data, series: [b, a] }, ['D_net_receive'], null).series[1])).toEqual(expected);
    const updated = labelled({ port: '1', device: 'switch-a' }, 99);
    updated.fields[1].config.displayNameFromDS = 'A renamed legend';
    expect(color(chartData({ ...data, series: [b, updated] }, ['D_net_receive'], 'switch-a', ['device']).series[0])).toEqual(expected);
    expect(a.fields[1].config.color).toBeUndefined();
    expect(overview.series[0].fields[0].config.color).toBeUndefined();
  });

  it.each(['dark', 'light'] as const)('applies the app colors through native Grafana field config with the stock %s theme', async (mode) => {
    const { chartData, chartFieldConfig } = await import('../src/domain-scene');
    const data = chartData(panelData(['D_test_load']), ['D_test_load'], null);
    const numeric = data.series[0].fields[0];
    const frames = applyFieldOverrides({ data: data.series, fieldConfig: chartFieldConfig('percent'), replaceVariables: (value) => value, theme: createTheme({ colors: { mode } }) });
    expect(frames[0].fields[0].config.color).toEqual(numeric.config.color);
    expect(frames[0].fields[0].display?.(1).color?.toLowerCase()).toBe(numeric.config.color?.fixedColor?.toLowerCase());
  });

  it('isolates native panel cleanup from the runner and other consumers', async () => {
    const { chartData } = await import('../src/domain-scene');
    const data = panelData(['D_spark_running']);
    const first = chartData(data, ['D_spark_running'], null);
    const second = chartData(data, ['D_spark_running'], null);
    first.series[0].fields[0].values.length = 0;
    expect(data.series[0].fields[0].values).toEqual([1]);
    expect(second.series[0].fields[0].values).toEqual([1]);
  });

  it('keeps out-of-window samples out of both the plot and the native legend', async () => {
    const { chartData } = await import('../src/domain-scene');
    const data = panelData(['D_spark_running']);
    data.series = [toDataFrame({ refId: 'D_spark_running', fields: [
      { name: 'Time', type: 'time', values: [-1000, 500, 2000] },
      { name: 'Value', type: 'number', values: [77, 3, 99] },
    ] })];
    const scoped = chartData(data, ['D_spark_running'], null);
    expect(scoped.series[0].fields[0].values).toEqual([500]);
    expect(scoped.series[0].fields[1].values).toEqual([3]);
    expect(scoped.series[0].length).toBe(1);
    expect(data.series[0].fields[1].values).toEqual([77, 3, 99]);
  });

  it('filters labelled frames without treating an unknown entity as every resource', async () => {
    const { chartData } = await import('../src/domain-scene');
    const data = panelData(['D_net_receive']);
    data.series = ['a', 'b'].map((device) => toDataFrame({ refId: 'D_net_receive', fields: [{ name: 'Time', type: 'time', values: [1000] }, { name: 'Value', type: 'number', values: [7], labels: { device } }] }));
    expect(chartData(data, ['D_net_receive'], 'a', ['device']).series).toHaveLength(1);
    expect(chartData(data, ['D_net_receive'], 'a', ['device']).series[0].fields[1].labels).toEqual({ device: 'a' });
    expect(chartData(data, ['D_net_receive'], 'missing', ['device']).series).toHaveLength(0);
    expect(chartData(data, ['D_net_receive'], 'a', ['instance']).series).toHaveLength(0);
    expect(chartData(data, ['D_net_receive'], null, ['device']).series).toHaveLength(2);
    expect(data.series).toHaveLength(2);
  });

  it('preserves a legacy single-error response while narrowing it', () => {
    const scoped = scopeData(panelData(['A'], { state: LoadingState.Error, error: { refId: 'A', message: 'failed' } }), new Set(['A']));
    expect(scoped.state).toBe(LoadingState.Error);
    expect(scoped.errors?.[0].message).toBe('failed');
  });
});
