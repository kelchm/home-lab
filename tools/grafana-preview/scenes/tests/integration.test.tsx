vi.mock('@shared/domains/registry', () => import('./support/domains'));
// The other suites stub the renderer. This one proves the host against the real shared HomeView.
import { cleanup, screen } from '@testing-library/react';
import { act } from 'react';
import { LoadingState } from '@grafana/data';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { installFakes, mountAt, navigate, runs, useRecordedData } from './support/harness';

vi.mock('@shared/HomeView', () => import('@real-shared/HomeView'));

const view = (name: string) => document.querySelector<HTMLElement>(`[data-view="${name}"]`);
const system = (name: string) => document.querySelector<HTMLAnchorElement>(`[data-system="${name}"]`);

beforeEach(() => {
  installFakes();
  useRecordedData();
});
afterEach(() => cleanup());

describe('with the real shared renderer', () => {
  it('draws every Overview view and marks the data as recorded', async () => {
    await mountAt('');
    for (const name of ['navigation', 'briefing', 'activity', 'review', 'machines', 'checks']) {
      expect(view(name)?.dataset.mode).toBe('recorded');
    }
    expect(view('network-summary')).toBeNull();
  });

  it('opens the Home inspector with the visible card retained evidence after failure', async () => {
    await mountAt('');
    await act(async () => runs[0].respond({ state: LoadingState.Error, series: [], errors: [{ refId: 'M_E', message: 'Refresh failed' }] }));
    expect(view('machines')?.dataset.freshness).toBe('stale');
    await navigate('?inspect=machines&inspectTab=data');
    expect(await screen.findByText('Inspect: Machines')).toBeTruthy();
    const inspector = screen.getByTestId('compare-inspector');
    expect(inspector.textContent).toMatch(/\d+ frames in this scope/);
    expect(inspector.textContent).toContain('Retained values were received');
  });

  it('derives the accepted briefing from recorded frames', async () => {
    await mountAt('');
    expect(view('briefing')?.dataset.freshness).toBe('fresh');
    const briefing = view('briefing')?.textContent ?? '';
    expect(briefing).toContain('No action needed at window end');
    expect(briefing).toContain('5 resolved');
    expect(briefing).toContain('3 worth a look');
    expect(briefing).toContain('1 maintenance');
  });

  it('shows all eight systems as internal destinations', async () => {
    await mountAt('');
    expect(document.querySelectorAll('[data-system]')).toHaveLength(8);
    expect(document.querySelectorAll('[data-outside]')).toHaveLength(0);
    expect(new URL(system('Network')!.href).searchParams.get('focus')).toBe('Network');
  });

  it('uses natural height everywhere and keeps the disclosure closed until asked', async () => {
    await mountAt('');
    expect(document.querySelectorAll('[data-constrained]')).toHaveLength(0);
    expect(document.querySelector('details.checks')?.hasAttribute('open')).toBe(false);
  });

  it('keeps source=live through focus links and marks the data as live', async () => {
    await mountAt('?source=live', 'live');
    expect(view('review')?.dataset.mode).toBe('live');
    expect(new URL(system('Network')!.href).searchParams.get('source')).toBe('live');
    await navigate('?source=live&focus=Network&from=now-24h&to=now');
    expect(document.querySelector<HTMLElement>('[data-domain=net]')?.dataset.mode).toBe('live');
    expect(new URL(system('Overview')!.href).searchParams.get('source')).toBe('live');
  });
});
