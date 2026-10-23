/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EngineSerials, MAX_SERIAL_LENGTH, normaliseSerial } from './engine-serials';

beforeEach(() => localStorage.clear());

describe('normaliseSerial', () => {
  it('upper-cases and drops spaces and stray characters', () => {
    expect(normaliseSerial(' r576 ')).toBe('R576');
    expect(normaliseSerial('rvac-12!')).toBe('RVAC-12');
    expect(normaliseSerial('   ')).toBe('');
  });

  it('caps the length', () => {
    expect(normaliseSerial('R'.repeat(20))).toHaveLength(MAX_SERIAL_LENGTH);
  });
});

describe('EngineSerials', () => {
  it('stores cleaned serials and forgets empty ones', () => {
    const serials = new EngineSerials('starship');
    expect(serials.get('ss_e6')).toBeUndefined();
    expect(serials.isEmpty).toBe(true);

    serials.set('ss_e6', 'r576');
    expect(serials.get('ss_e6')).toBe('R576');
    expect(serials.isEmpty).toBe(false);

    serials.set('ss_e6', '  ');
    expect(serials.get('ss_e6')).toBeUndefined();
    expect(serials.isEmpty).toBe(true);
  });

  it('persists per vehicle across instances', () => {
    new EngineSerials('starship').set('ss_e5', 'R575');

    expect(new EngineSerials('starship').get('ss_e5')).toBe('R575');
    expect(new EngineSerials('superheavy').get('ss_e5')).toBeUndefined();
  });

  it('clear removes every serial and is saved', () => {
    const serials = new EngineSerials('starship');
    serials.set('ss_e1', 'R101');
    serials.set('ss_e4', 'R404');
    serials.clear();

    expect(serials.isEmpty).toBe(true);
    expect(new EngineSerials('starship').isEmpty).toBe(true);
  });

  it('notifies subscribers on real changes only', () => {
    const serials = new EngineSerials('starship');
    const listener = vi.fn();
    const unsubscribe = serials.subscribe(listener);

    serials.set('ss_e6', 'R576');
    serials.set('ss_e6', 'r576'); // same serial once cleaned
    serials.set('ss_e2', ''); // was already empty
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    serials.clear();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('ignores corrupt or invalid saved data', () => {
    localStorage.setItem('engine-serials.starship', '{not json');
    expect(new EngineSerials('starship').isEmpty).toBe(true);

    localStorage.setItem('engine-serials.starship', JSON.stringify({ ss_e1: 42, ss_e2: '!!', ss_e3: 'r3' }));
    const serials = new EngineSerials('starship');
    expect(serials.get('ss_e1')).toBeUndefined();
    expect(serials.get('ss_e2')).toBeUndefined();
    expect(serials.get('ss_e3')).toBe('R3');
  });

  it('keeps working when storage is unavailable', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const serials = new EngineSerials('starship');
    serials.set('ss_e6', 'R576');
    expect(serials.get('ss_e6')).toBe('R576');
    setItem.mockRestore();
  });
});

describe('EngineSerials with persist: false', () => {
  it('never touches localStorage and keeps changes in memory', () => {
    localStorage.setItem('engine-serials.starship', JSON.stringify({ ss_e1: 'R100' }));
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem');
    try {
      const serials = new EngineSerials('starship', {
        persist: false,
        initial: { ss_e6: ' r576 ', ss_e2: '!!' },
      });
      const listener = vi.fn();
      serials.subscribe(listener);

      expect(serials.get('ss_e1')).toBeUndefined();
      expect(serials.snapshot()).toEqual({ ss_e6: 'R576' });
      serials.set('ss_e3', 'R300');
      serials.replaceAll({ ss_e4: 'R400' });
      serials.clear();
      expect(serials.isEmpty).toBe(true);
      expect(listener).toHaveBeenCalledTimes(3);

      expect(getItem).not.toHaveBeenCalled();
      expect(setItem).not.toHaveBeenCalled();
      expect(removeItem).not.toHaveBeenCalled();
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
      removeItem.mockRestore();
    }
  });
});
