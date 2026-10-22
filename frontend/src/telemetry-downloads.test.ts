/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { StateManager } from './state/state-manager';
import { TelemetryDownloads } from './telemetry-downloads';
import type { TelemetryRecord } from './core/types';

function record(sequence: number): TelemetryRecord {
  return {
    sequence_number: sequence,
    frame_number: sequence * 30,
    mission_elapsed_time: `T+00:00:0${sequence}`,
    mission_elapsed_time_raw: null,
    speed_left: { value: 100, unit: 'KM/H', status: 'available' },
    speed_right: { value: 200, unit: 'KM/H', status: 'available' },
    altitude_left: { value: 1, unit: 'KM', status: 'available' },
    altitude_right: { value: 2, unit: 'KM', status: 'available' },
    stage_left_label: null,
    stage_right_label: null,
    stage_separation_text: null,
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'super_heavy',
    separation_state: 'pre_separation',
    t_zero_found: true,
    stage_sep_found: false,
    starship_engines: {},
    superheavy_engines: {},
    detection_accuracy: { starship: 1, superheavy: 1 },
    timestamp: sequence * 1000,
  };
}

describe('TelemetryDownloads', () => {
  let saved: { name: string; blob: Blob }[];
  const { createObjectURL, revokeObjectURL } = URL;

  beforeEach(() => {
    saved = [];
    let lastBlob: Blob | null = null;
    // jsdom has no object URLs; capture the blob instead
    URL.createObjectURL = (blob: Blob) => {
      lastBlob = blob;
      return 'blob:test';
    };
    URL.revokeObjectURL = () => {};
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push({ name: this.download, blob: lastBlob! });
    });
  });

  afterEach(() => {
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    vi.restoreAllMocks();
  });

  function setup() {
    const container = document.createElement('div');
    const manager = new StateManager();
    new TelemetryDownloads(container, manager);
    const button = (format: string) =>
      container.querySelector<HTMLButtonElement>(`[data-format="${format}"]`)!;
    const count = () => container.querySelector('.telemetry-downloads__count')!.textContent;
    return { manager, button, count };
  }

  it('is disabled until a record arrives, then shows the count', () => {
    const { manager, button, count } = setup();
    expect(button('flight').disabled).toBe(true);
    expect(count()).toBe('0 records');

    manager.handleMessage({ type: 'telemetry', payload: record(1) });
    expect(button('flight').disabled).toBe(false);
    expect(button('raw').disabled).toBe(false);
    expect(count()).toBe('1 record');
  });

  it('downloads the flight file and the raw records', async () => {
    const { manager, button } = setup();
    manager.handleMessage({ type: 'telemetry', payload: record(1) });
    manager.handleMessage({ type: 'telemetry', payload: record(2) });

    button('flight').click();
    button('raw').click();

    expect(saved.map((s) => s.name)).toEqual([
      expect.stringMatching(/^telemetry-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d\.json$/),
      expect.stringMatching(/^telemetry-.*-raw\.json$/),
    ]);
    const flight = JSON.parse(await saved[0].blob.text());
    expect(flight.map((r: { frame_number: number }) => r.frame_number)).toEqual([30, 60]);
    // Pre-separation: both vehicles carry the stack reading
    expect(flight[0].vehicles.starship.speed).toBe(100);
    expect(flight[0].vehicles.superheavy.speed).toBe(100);
    expect(JSON.parse(await saved[1].blob.text())).toEqual([record(1), record(2)]);
  });
});
