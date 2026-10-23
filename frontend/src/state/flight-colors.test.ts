/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FlightColors, isNearLiveColor, LIVE_COLOR, PRESET_COLORS } from './flight-colors';

beforeEach(() => localStorage.clear());

describe('FlightColors', () => {
  it('falls back to the caller default until a colour is picked', () => {
    const colors = new FlightColors('telemetry');
    expect(colors.get('ift-13', '#e91e63')).toBe('#e91e63');
    expect(colors.isCustom('ift-13')).toBe(false);

    colors.set('ift-13', '#4FC3F7');
    expect(colors.get('ift-13', '#e91e63')).toBe('#4fc3f7');
    expect(colors.isCustom('ift-13')).toBe(true);
  });

  it('persists picks per namespace across instances', () => {
    new FlightColors('telemetry').set('ift-13', '#81c784');

    expect(new FlightColors('telemetry').get('ift-13', '#000000')).toBe('#81c784');
    expect(new FlightColors('trajectory').get('ift-13', '#000000')).toBe('#000000');
  });

  it('reset restores the default and is saved', () => {
    const colors = new FlightColors('trajectory');
    colors.set('ship39', '#81c784');
    colors.reset('ship39');

    expect(colors.get('ship39', '#4fc3f7')).toBe('#4fc3f7');
    expect(new FlightColors('trajectory').isCustom('ship39')).toBe(false);
  });

  it('notifies subscribers on real changes only', () => {
    const colors = new FlightColors('telemetry');
    const listener = vi.fn();
    const unsubscribe = colors.subscribe(listener);

    colors.set('a', '#81c784');
    colors.set('a', '#81c784');
    colors.set('a', 'red');
    colors.reset('b');
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    colors.reset('a');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('ignores corrupt or invalid saved data', () => {
    localStorage.setItem('flight-colors.telemetry', '{not json');
    expect(new FlightColors('telemetry').get('a', '#111111')).toBe('#111111');

    localStorage.setItem('flight-colors.telemetry', JSON.stringify({ a: 'blue', b: '#ABCDEF', c: 3 }));
    const colors = new FlightColors('telemetry');
    expect(colors.get('a', '#111111')).toBe('#111111');
    expect(colors.get('b', '#111111')).toBe('#abcdef');
    expect(colors.isCustom('c')).toBe(false);
  });

  it('still works when storage throws', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const colors = new FlightColors('telemetry');
    colors.set('a', '#81c784');
    expect(colors.get('a', '#111111')).toBe('#81c784');
    getItem.mockRestore();
    setItem.mockRestore();
  });
});

describe('FlightColors with persist: false', () => {
  it('never touches localStorage and keeps changes in memory', () => {
    localStorage.setItem('flight-colors.telemetry', JSON.stringify({ 'ift-13': '#81c784' }));
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem');
    try {
      const colors = new FlightColors('telemetry', {
        persist: false,
        initial: { 'ift-12': '#4FC3F7', bad: 'red' },
      });
      const listener = vi.fn();
      colors.subscribe(listener);

      expect(colors.get('ift-13', '#000000')).toBe('#000000');
      expect(colors.snapshot()).toEqual({ 'ift-12': '#4fc3f7' });
      colors.set('ift-13', '#e91e63');
      colors.reset('ift-12');
      expect(colors.snapshot()).toEqual({ 'ift-13': '#e91e63' });
      expect(listener).toHaveBeenCalledTimes(2);

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

describe('isNearLiveColor', () => {
  it('flags the live orange and close shades', () => {
    expect(isNearLiveColor(LIVE_COLOR)).toBe(true);
    expect(isNearLiveColor('#ff9030')).toBe(true);
  });

  it('accepts every preset', () => {
    expect(PRESET_COLORS.filter(isNearLiveColor)).toEqual([]);
  });
});
