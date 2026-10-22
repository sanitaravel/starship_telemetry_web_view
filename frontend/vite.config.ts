import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { composeVersion } from './src/core/app-version.ts';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));

/** Commit count of HEAD, or null when git or the repository is unavailable. */
function gitCommitCount(): number | null {
  try {
    const out = execSync('git rev-list --count HEAD', {
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    const count = Number.parseInt(out.toString().trim(), 10);
    return Number.isNaN(count) ? null : count;
  } catch {
    return null;
  }
}

// Same rule as backend/src/version.py: VERSION file + commit count
const appVersion = composeVersion(
  readFileSync(new URL('../VERSION', import.meta.url), 'utf-8'),
  gitCommitCount(),
);

export default defineConfig({
  root: '.',
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/ws': {
        target: 'http://127.0.0.1:8000',
        ws: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
      },
    },
  },
});
