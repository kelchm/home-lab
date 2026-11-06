vi.mock('@shared/domains/registry', () => import('./support/domains'));
vi.mock('@grafana/i18n', async (importOriginal) => ({
  ...await importOriginal<typeof import('@grafana/i18n')>(),
  initPluginTranslations: vi.fn().mockRejectedValue(new Error('Translations unavailable')),
}));
import { cleanup, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { installFakes, mountRoot } from './support/harness';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('mounts the app with default text after translation initialization rejects', async () => {
  installFakes();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  await mountRoot('');
  expect(await screen.findByText('Observability')).toBeTruthy();
  expect(screen.queryByText('Loading Lab')).toBeNull();
  expect(warn).toHaveBeenCalledWith('Lab translations could not be loaded; using default text.', expect.any(Error));
});
