import { useMemo, useSyncExternalStore } from 'react';
import { locationUtil, type PanelProps } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { HomeView, modeOfDatasource } from './HomeView';
import type { PanelOptions } from './types';

const FOCUS_PARAM = 'var-focus';

const subscribe = (listener: () => void) => {
  const subscription = locationService.getLocationObservable().subscribe(listener);
  return () => subscription.unsubscribe();
};

const zoneOf = (timeZone: string) => (timeZone === 'utc' ? 'UTC' : timeZone === 'browser' ? Intl.DateTimeFormat().resolvedOptions().timeZone : timeZone);

/** The native host: every link keeps the URL as it stands and changes only the focus variable, and Grafana owns the rest. */
export function NativePanel({ data, options, timeRange, timeZone, width, replaceVariables }: PanelProps<PanelOptions>) {
  const location = useSyncExternalStore(subscribe, () => locationService.getLocation());
  const links = useMemo(
    () => ({
      Overview: locationUtil.getUrlForPartial(location, { [FOCUS_PARAM]: 'Overview' }),
      Network: locationUtil.getUrlForPartial(location, { [FOCUS_PARAM]: 'Network' }),
    }),
    [location]
  );
  return (
    <HomeView
      data={data}
      timeRange={timeRange}
      view={options.view}
      focus={replaceVariables(options.focus)}
      links={links}
      constrained={options.constrained}
      width={width}
      mode={modeOfDatasource(replaceVariables('$datasource'))}
      timeZone={zoneOf(timeZone)}
    />
  );
}
