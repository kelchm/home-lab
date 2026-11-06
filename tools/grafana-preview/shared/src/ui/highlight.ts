import { useSyncExternalStore } from 'react';
import type { Span } from '../model/home';
import type { SystemId } from '../model/systems';

export interface Highlight {
  system: SystemId;
  spans: Span[];
}

// Review and Activity can be separate panels. Module state crosses that without either host being involved, or any shared DOM.
let current: Highlight | null = null;
const listeners = new Set<() => void>();

export function setHighlight(next: Highlight | null) {
  current = next;
  listeners.forEach((listener) => listener());
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export const useHighlight = () =>
  useSyncExternalStore(
    subscribe,
    () => current,
    () => null
  );
