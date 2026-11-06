import { locationService } from '@grafana/runtime';
import { useCallback, useRef, useState, useSyncExternalStore } from 'react';

const subscribeToLocation = (onChange: () => void) => {
  const subscription = locationService.getLocationObservable().subscribe(onChange);
  return () => subscription.unsubscribe();
};

/** Grafana's location as a value. Scenes uses router v6 and core exposes v5, so a service subscription avoids depending on either. */
export const useAppLocation = () => useSyncExternalStore(subscribeToLocation, () => locationService.getLocation());

/** A callback ref, because the measured element can appear after mount, once data has arrived. An effect would run once and miss it. */
export function useElementWidth<T extends HTMLElement>() {
  const [width, setWidth] = useState<number>();
  const observer = useRef<ResizeObserver>(null);
  const ref = useCallback((element: T | null) => {
    observer.current?.disconnect();
    if (element) {
      observer.current = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
      observer.current.observe(element);
    }
  }, []);
  return [ref, width] as const;
}
