/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import { EngineSerials } from '../../state/engine-serials';
import {
  useVisualizer,
  E6,
  E1,
  starshipGroup,
  starshipEngine,
  gear,
  field,
  type,
  tooltip,
  hover,
  reload,
  destroyVisualizer,
  rebuildVisualizer,
} from './visualizer.harness';

useVisualizer();

describe('Starship engine serials on the diagram', () => {
  it('circles keep their E-numbers when a serial is set', () => {
    gear().click();
    type(field('ss_e6'), '576');

    expect(starshipEngine(E6)).toEqual({ text: 'E6', ariaLabel: 'E6, serial R576' });
    expect(starshipEngine(E1)).toEqual({ text: 'E1', ariaLabel: 'E1' });
  });

  it('hovering an engine with a serial shows it in the tooltip', () => {
    new EngineSerials('starship').set('ss_e6', 'R576');
    reload();

    expect(tooltip().hidden).toBe(true);
    hover(E6);
    expect(tooltip().hidden).toBe(false);
    expect(tooltip().textContent).toBe('E6R576');
    expect(tooltip().querySelector('.engine-tooltip__serial')!.textContent).toBe('R576');

    starshipGroup(E6).ownerSVGElement!.dispatchEvent(new Event('pointerleave'));
    expect(tooltip().hidden).toBe(true);
  });

  it('shows no tooltip over an engine without a serial', () => {
    new EngineSerials('starship').set('ss_e6', 'R576');
    reload();

    hover(E1);
    expect(tooltip().hidden).toBe(true);
  });

  it('an open tooltip follows a serial edited meanwhile', () => {
    gear().click();
    type(field('ss_e6'), '576');
    hover(E6);
    expect(tooltip().textContent).toBe('E6R576');

    type(field('ss_e6'), '577');
    expect(tooltip().textContent).toBe('E6R577');

    type(field('ss_e6'), '');
    expect(tooltip().hidden).toBe(true);
  });

  it('removes its tooltip when destroyed', () => {
    expect(document.querySelectorAll('.engine-tooltip')).toHaveLength(1);
    destroyVisualizer();
    expect(document.querySelectorAll('.engine-tooltip')).toHaveLength(0);
    rebuildVisualizer(); // for afterEach
  });
});
