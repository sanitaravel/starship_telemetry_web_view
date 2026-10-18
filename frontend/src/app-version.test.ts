import { describe, it, expect } from 'vitest';
import { composeVersion, appVersion } from './app-version';

describe('composeVersion', () => {
  it('appends the commit count as the fourth number', () => {
    expect(composeVersion('0.1.0\n', 64)).toBe('0.1.0.64');
  });

  it('uses the release version alone without a build number', () => {
    expect(composeVersion('1.2.3', null)).toBe('1.2.3');
  });

  it('rejects a VERSION that is not MAJOR.MINOR.PATCH', () => {
    expect(() => composeVersion('0.1', 5)).toThrow(/MAJOR\.MINOR\.PATCH/);
  });
});

describe('appVersion', () => {
  it('falls back to "dev" when no version was injected', () => {
    expect(appVersion()).toBe('dev');
  });
});
