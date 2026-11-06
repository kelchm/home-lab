// Keeps host lifecycle tests independent of the visuals while emitting the real resolver evidence callback.
import { useLayoutEffect, useMemo } from 'react';
import type { HomeEvidence } from '../../../shared/src/HomeView';
import { rangeKey, useResolvedData } from '@shared/lifecycle';
import { QUERIES } from '@shared/model/catalog';
import type { HomeViewProps, View } from '@shared/types';

export const lastProps = new Map<View, HomeViewProps>();

export function HomeView(props: HomeViewProps & { onEvidence?: (evidence: HomeEvidence) => void }) {
  lastProps.set(props.view, props);
  const { data, timeRange, view, onEvidence } = props;
  const source = props.mode ?? 'unknown';
  const resolved = useResolvedData(data, QUERIES[view], source, rangeKey(timeRange));
  const evidence = useMemo<HomeEvidence>(() => ({ data: { ...data, timeRange, series: resolved.series }, resolved, mode: source, timeRange }), [data, timeRange, resolved, source]);
  useLayoutEffect(() => { onEvidence?.(evidence); }, [onEvidence, evidence]);
  return (
    <div data-view={props.view} data-frames={props.data.series.length} data-state={props.data.state}>
      {props.view === 'navigation' && Object.entries(props.links).map(([name, href]) => <a key={name} href={href}>{name}</a>)}
    </div>
  );
}
