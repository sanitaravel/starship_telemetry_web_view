import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // Reuse workers across test files; tests must restore any globals/timers they change.
    isolate: false,
  },
});
