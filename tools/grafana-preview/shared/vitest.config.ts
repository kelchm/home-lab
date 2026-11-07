import { defineConfig } from 'vitest/config';

// Node by default, because the model and lifecycle are DOM-free. view.test.tsx opts into jsdom itself.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: { include: ['tests/**/*.test.{ts,tsx}', 'src/domains/**/*.test.{ts,tsx}'] },
});
