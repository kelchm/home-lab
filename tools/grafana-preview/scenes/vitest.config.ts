import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: [
      // Lifecycle tests record what the host passes; visuals belong to shared. tests/integration.test.tsx opts into the real renderer with vi.mock.
      { find: '@real-shared/HomeView', replacement: path.resolve('../shared/src/HomeView.tsx') },
      { find: '@shared/HomeView', replacement: path.resolve('tests/support/HomeViewStub.tsx') },
      { find: '@shared', replacement: path.resolve('../shared/src') },
    ],
    dedupe: ['react', 'react-dom', 'rxjs', '@grafana/data', '@grafana/runtime', '@grafana/ui', '@grafana/scenes'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['tests/support/setup.ts'],
    server: { deps: { inline: [/@grafana\//, 'rc-picker', 'rc-util'] } },
    testTimeout: 30000,
    pool: 'forks',
  },
});
