import { describe, it, expect } from 'vitest';
import { SERIAL_PREFIX, withPrefix, withoutPrefix } from './settings-popover';

describe('serial prefix', () => {
  it('adds the R to what was typed', () => {
    expect(SERIAL_PREFIX).toBe('R');
    expect(withPrefix('576')).toBe('R576');
    expect(withPrefix(' 57 6 ')).toBe('R576');
  });

  it('does not double an R typed out of habit', () => {
    expect(withPrefix('R576')).toBe('R576');
    expect(withPrefix('r576')).toBe('R576');
  });

  it('gives no serial for an empty field', () => {
    expect(withPrefix('')).toBe('');
    expect(withPrefix('  ')).toBe('');
    expect(withPrefix('R')).toBe('');
  });

  it('shows a stored serial without its R', () => {
    expect(withoutPrefix('R576')).toBe('576');
    expect(withoutPrefix(undefined)).toBe('');
    expect(withoutPrefix('X12')).toBe('X12'); // stored before the prefix existed
  });
});
