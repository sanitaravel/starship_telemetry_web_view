/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import type { CurrentMission as CurrentMissionType } from '../../state/current-mission';
import type { TimelineEvent } from '../../domain/timeline/types';
import { FakeMissionsApi } from '../../api/fake-missions';
import { COUNTDOWN_PASTE, FLIGHT_PASTE } from '../../domain/timeline/fixtures';

// The editor is a module-level singleton and workers are shared (isolate:
// false), so this file loads a private copy of it and removes its panel when done.
let editor: typeof import('./timeline-editor');
let currentModule: typeof import('../../state/current-mission');
let serialsModule: typeof import('../../state/engine-serials');
let settings: typeof import('./settings-popover');

beforeAll(async () => {
  vi.resetModules();
  editor = await import('./timeline-editor');
  currentModule = await import('../../state/current-mission');
  serialsModule = await import('../../state/engine-serials');
  settings = await import('./settings-popover');
});

afterAll(() => {
  document.dispatchEvent(new Event('pointerdown'));
  document.querySelectorAll('.timeline-editor, .engine-settings').forEach((el) => el.remove());
  vi.resetModules();
});

let api: FakeMissionsApi;
let mission: CurrentMissionType;
let anchor: HTMLButtonElement;

const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

beforeEach(() => {
  localStorage.clear();
  api = new FakeMissionsApi();
  const serials = {
    starship: new serialsModule.EngineSerials('starship'),
    superheavy: new serialsModule.EngineSerials('superheavy'),
  };
  mission = new currentModule.CurrentMission(serials, api, 0);
  anchor = document.createElement('button');
  document.body.appendChild(anchor);
});

afterEach(() => {
  document.dispatchEvent(new Event('pointerdown'));
  anchor.remove();
});

/** The open editor panel. */
const panel = () => [...document.querySelectorAll<HTMLElement>('.timeline-editor')].find((p) => !p.hidden)!;
const box = (section: string) => panel().querySelector<HTMLTextAreaElement>(`[data-section="${section}"]`)!;
const rows = () => [...panel().querySelectorAll<HTMLTableRowElement>('tbody tr')];
const saveButton = () => [...panel().querySelectorAll('button')].find((b) => b.textContent === 'Save')!;

function paste(section: string, text: string) {
  const area = box(section);
  area.value = text;
  area.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('timeline editor', () => {
  it('shows the two pasted tables as one preview, in time order with both T-0 rows', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('countdown', COUNTDOWN_PASTE);
    paste('flight', FLIGHT_PASTE);

    expect(rows()).toHaveLength(32);
    const times = rows().map((r) => r.cells[0].textContent);
    expect(times[0]).toBe('T-00:50:00');
    expect(times.slice(11, 13)).toEqual(['T+00:00:00', 'T+00:00:00']);
    expect(rows()[11].querySelector<HTMLInputElement>('input')!.value).toBe('Excitement guaranteed');
    expect(rows()[12].querySelector<HTMLInputElement>('input')!.value).toBe('Liftoff');
    expect(panel().textContent).toContain('32 events');
  });

  it('lists lines it could not read, with their line numbers', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('flight', '00:00:00\tLiftoff\nnot a row');

    const warnings = [...panel().querySelectorAll('.timeline-editor__warnings li')].map((li) => li.textContent);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('line 2');
    expect(warnings[0]).toContain('not a row');
    expect(rows()).toHaveLength(1);
  });

  it('saves the edited rows through the mission', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('flight', '00:00:00\tLiftoff\n00:00:58\tMax Q (peak stress)');

    const first = rows()[0];
    const text = first.querySelector<HTMLInputElement>('input')!;
    text.value = 'Liftoff!';
    text.dispatchEvent(new Event('input', { bubbles: true }));
    const vehicle = first.querySelectorAll('select')[0];
    vehicle.value = 'starship';
    vehicle.dispatchEvent(new Event('change', { bubbles: true }));
    saveButton().click();
    await mission.flush();

    expect(panel()).toBeUndefined(); // closed
    expect(api.files.get('ift-15')!.timeline).toEqual([
      { id: '0-liftoff', t: 0, event: 'Liftoff!', vehicle: 'starship', auto: { kind: 'rule', rule: 'liftoff' } },
      { id: '1-max-q-peak-stress', t: 58, event: 'Max Q (peak stress)', vehicle: 'both', auto: { kind: 'hud', label: 'MAX Q' } },
    ]);
  });

  it('can change the source: HUD label text and rule choice', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('flight', '00:00:58\tMax Q');
    const source = rows()[0].querySelector('.timeline-editor__source')!;

    const label = source.querySelector<HTMLInputElement>('input')!;
    label.value = 'MAX-Q';
    label.dispatchEvent(new Event('input', { bubbles: true }));
    const kind = source.querySelector('select')!;
    kind.value = 'rule';
    kind.dispatchEvent(new Event('change', { bubbles: true }));
    const rule = source.querySelectorAll('select')[1];
    rule.value = 'sh_meco';
    rule.dispatchEvent(new Event('change', { bubbles: true }));
    saveButton().click();
    await mission.flush();

    expect(api.files.get('ift-15')!.timeline![0].auto).toEqual({ kind: 'rule', rule: 'sh_meco' });
  });

  it('removes a row', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('flight', '00:00:00\tLiftoff\n00:00:58\tMax Q');
    rows()[0].querySelector<HTMLButtonElement>('.timeline-editor__remove')!.click();

    expect(rows().map((r) => r.querySelector<HTMLInputElement>('input')!.value)).toEqual(['Max Q']);
  });

  it('refuses to save an empty event text or HUD label', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('flight', '00:00:58\tMax Q');
    const text = rows()[0].querySelector<HTMLInputElement>('input')!;
    text.value = '  ';
    text.dispatchEvent(new Event('input', { bubbles: true }));

    expect(saveButton().disabled).toBe(true);
    expect(panel().querySelector('[role="alert"]')!.textContent).toContain('needs a text');
  });

  it('shows the mission’s saved timeline for editing when nothing is pasted', async () => {
    const saved: TimelineEvent[] = [
      { id: '0-liftoff', t: 0, event: 'Liftoff', vehicle: 'both', auto: { kind: 'rule', rule: 'liftoff' } },
    ];
    await mission.choose('IFT 15');
    mission.setTimeline(saved);
    await mission.flush();

    editor.toggleTimelineEditor(anchor, mission);
    expect(rows()).toHaveLength(1);
    expect(rows()[0].querySelector<HTMLInputElement>('input')!.value).toBe('Liftoff');
    expect(saveButton().disabled).toBe(false);
  });

  it('clearing the timeline saves an empty one', async () => {
    await mission.choose('IFT 15');
    mission.setTimeline([{ id: '0', t: 0, event: 'Liftoff', vehicle: 'both', auto: { kind: 'manual' } }]);
    await mission.flush();

    editor.toggleTimelineEditor(anchor, mission);
    [...panel().querySelectorAll('button')].find((b) => b.textContent === 'Clear timeline')!.click();
    saveButton().click();
    await mission.flush();

    expect(api.files.get('ift-15')!.timeline).toEqual([]);
  });

  it('needs a named mission', async () => {
    editor.toggleTimelineEditor(anchor, mission);
    paste('flight', '00:00:00\tLiftoff');

    expect(saveButton().disabled).toBe(true);
    expect(panel().textContent).toContain('Name the mission first');
    saveButton().click();
    await settle();
    expect(api.saves).toHaveLength(0);
  });

  it('closes on Escape and when clicking outside, and toggles on its anchor', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    expect(panel()).toBeDefined();
    box('flight').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(panel()).toBeUndefined();

    editor.toggleTimelineEditor(anchor, mission);
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(panel()).toBeUndefined();

    editor.toggleTimelineEditor(anchor, mission);
    editor.toggleTimelineEditor(anchor, mission);
    expect(panel()).toBeUndefined();
  });
});

describe('Timeline button in the engine settings', () => {
  it('opens the editor in place of the settings panel, with the timeline count', async () => {
    await mission.choose('IFT 15');
    mission.setTimeline([{ id: '0', t: 0, event: 'Liftoff', vehicle: 'both', auto: { kind: 'manual' } }]);
    await mission.flush();
    const serials = new serialsModule.EngineSerials('starship');
    settings.toggleEngineSettings(anchor, { title: 'Starship engines', groups: [], serials, mission });

    const settingsPanel = [...document.querySelectorAll<HTMLElement>('.engine-settings')].find((p) => !p.hidden)!;
    const button = settingsPanel.querySelector<HTMLButtonElement>('.engine-settings__timeline')!;
    expect(button.textContent).toBe('Timeline (1)');
    button.click();

    expect(settingsPanel.hidden).toBe(true);
    expect(panel()).toBeDefined();
    expect(rows()).toHaveLength(1);
  });

  it('is not offered without a mission (overlays have none)', () => {
    const serials = new serialsModule.EngineSerials('starship');
    settings.toggleEngineSettings(anchor, { title: 'Starship engines', groups: [], serials });
    const settingsPanel = [...document.querySelectorAll<HTMLElement>('.engine-settings')].find((p) => !p.hidden)!;
    expect(settingsPanel.querySelector('.engine-settings__timeline')).toBeNull();
  });

  it('gives every form field a name, and the boxes an id', async () => {
    await mission.choose('IFT 15');
    editor.toggleTimelineEditor(anchor, mission);
    paste('countdown', COUNTDOWN_PASTE);
    const fields = [...panel().querySelectorAll<HTMLElement>('input, select, textarea')];
    expect(fields.length).toBeGreaterThan(10);
    for (const field of fields) expect((field as HTMLInputElement).name).not.toBe('');
    expect(box('countdown').id).not.toBe('');
    const names = fields.map((f) => (f as HTMLInputElement).name);
    expect(new Set(names).size).toBe(names.length);
  });

  it('re-places itself when the panel changes size', async () => {
    const observers: { callback: () => void }[] = [];
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: () => void) {
          observers.push({ callback });
        }
        observe() {}
        disconnect() {}
      },
    );
    try {
      vi.resetModules();
      const fresh = await import('./timeline-editor');
      await mission.choose('IFT 15');
      fresh.toggleTimelineEditor(anchor, mission);
      const el = panel();
      el.style.top = '';
      observers.forEach((o) => o.callback());
      expect(el.style.top).not.toBe('');
      fresh.toggleTimelineEditor(anchor, mission);
    } finally {
      vi.unstubAllGlobals();
      document.querySelectorAll('.timeline-editor').forEach((p) => p.remove());
    }
  });
});
