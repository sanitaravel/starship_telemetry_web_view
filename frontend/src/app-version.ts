/**
 * Application version: MAJOR.MINOR.PATCH from the repo's VERSION file plus
 * the commit count as a fourth, build number (e.g. 0.1.0.64).
 *
 * vite.config.ts computes it at build time and injects it as __APP_VERSION__;
 * backend/src/core/version.py applies the same rule.
 */

const SEMVER = /^\d+\.\d+\.\d+$/;

/**
 * Join the release version and build number: '0.1.0' + 64 -> '0.1.0.64'.
 * Without a build number (no git) the release version is used alone.
 */
export function composeVersion(release: string, build: number | null): string {
  const base = release.trim();
  if (!SEMVER.test(base)) {
    throw new Error(`VERSION must hold MAJOR.MINOR.PATCH, got "${base}"`);
  }
  return build === null ? base : `${base}.${build}`;
}

/** The version this bundle was built as, or 'dev' if it was not injected. */
export function appVersion(): string {
  return typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : 'dev';
}
