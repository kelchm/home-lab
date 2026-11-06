// Stands in for the shared renderer so host lifecycle tests are independent of its visuals. It only records what the host passes.
import type { HomeViewProps, View } from '@shared/types';

export const lastProps = new Map<View, HomeViewProps>();

export function HomeView(props: HomeViewProps) {
  lastProps.set(props.view, props);
  return (
    <div data-view={props.view} data-frames={props.data.series.length} data-state={props.data.state}>
      {props.view === 'navigation' && Object.entries(props.links).map(([name, href]) => <a key={name} href={href}>{name}</a>)}
    </div>
  );
}
