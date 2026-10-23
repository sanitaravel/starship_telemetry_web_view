import type { PreviousFlightInfo } from '../../core/types';
import { createLogger } from '../../core/logger';
import { fetchPreviousFlightList, fetchPreviousFlightData, PreviousFlightData } from '../../api/previous-flights';
import { FlightColors } from '../../state/flight-colors';
import { createSwatchButton, toggleColorPopover } from '../../ui/color-picker';
import type { LoadedComparison } from './types';
import { COMPARE_COLORS } from './panels';

const logger = createLogger('time-series-graphs');

let nextCompareId = 0;

/** What the comparison list needs from the chart that shows it. */
export interface ComparisonHooks {
  /** Redraw the chart with the current comparisons and colours. */
  refresh(): void;
  /** Show or hide the "loading comparison data" overlay. */
  setLoading(loading: boolean): void;
}

/**
 * Previous flights offered for comparison: the checkbox list, the fetches
 * behind it and the flights currently loaded for overlay.
 *
 * Given `fixed` flights (overlays) it runs headless: no checkbox list, just
 * those flights in that order and in those colours, kept in memory only.
 */
export class Comparisons {
  private compareContainer: HTMLElement;
  private hooks: ComparisonHooks;
  private previousFlights: PreviousFlightInfo[] = [];
  private selectedCompareFilenames: Set<string> = new Set();
  private loadedComparisons: LoadedComparison[] = [];
  /** In-flight comparison fetches, keyed by filename. */
  private pendingLoads: Map<string, Promise<void>> = new Map();
  /** Fetched comparison flights, kept so re-selecting one needs no refetch. */
  private flightCache: Map<string, PreviousFlightData> = new Map();
  /** User-picked overlay colours, keyed by flight filename. */
  private colors: FlightColors;
  /** Swatch buttons in the compare list, keyed by flight filename. */
  private swatches: Map<string, HTMLButtonElement> = new Map();
  /** The fixed selection of a headless list, as [filename, colour]; null for the dashboard. */
  private readonly fixed: readonly [string, string][] | null;

  constructor(compareContainer: HTMLElement, hooks: ComparisonHooks, fixed: readonly [string, string][] | null = null) {
    this.compareContainer = compareContainer;
    this.hooks = hooks;
    this.fixed = fixed;
    this.colors = fixed
      ? new FlightColors('telemetry', { persist: false, initial: Object.fromEntries(fixed) })
      : new FlightColors('telemetry');
  }

  /** Fetch the flight list and start repainting on colour changes. */
  start(): void {
    if (this.fixed) {
      void this.loadFixed(this.fixed);
      return;
    }
    this.loadPreviousFlightList();
    this.colors.subscribe(() => this.onColorsChange());
  }

  /** Load the fixed flights (names from the flight list), then draw them in order. */
  private async loadFixed(fixed: readonly [string, string][]): Promise<void> {
    try {
      this.previousFlights = await fetchPreviousFlightList();
    } catch (err) {
      logger.error('Failed to load the previous flight list', { error: String(err) });
    }
    for (const [filename] of fixed) this.selectedCompareFilenames.add(filename);
    await Promise.all(fixed.map(([filename]) => this.loadComparison(filename)));
    // Fetches settle in any order; keep the snapshot's
    const order = fixed.map(([filename]) => filename);
    this.loadedComparisons.sort((a, b) => order.indexOf(a.filename) - order.indexOf(b.filename));
    this.hooks.refresh();
  }

  /** The flights currently overlaid, in load order. */
  get loaded(): LoadedComparison[] {
    return this.loadedComparisons;
  }

  /** The user's colour for a previous flight, else its palette colour by list position. */
  flightColor(filename: string): string {
    const index = Math.max(0, this.previousFlights.findIndex((f) => f.filename === filename));
    return this.colors.get(filename, COMPARE_COLORS[index % COMPARE_COLORS.length]);
  }

  /** Repaint swatches and overlays after a colour is picked or reset. */
  private onColorsChange(): void {
    for (const [filename, swatch] of this.swatches) {
      swatch.style.backgroundColor = this.flightColor(filename);
    }
    this.hooks.refresh();
  }

  /**
   * Load the list of available previous flights and populate the compare checkboxes.
   */
  private async loadPreviousFlightList(): Promise<void> {
    this.previousFlights = await fetchPreviousFlightList();

    for (let i = 0; i < this.previousFlights.length; i++) {
      const flight = this.previousFlights[i];
      const checkboxId = `time-series-graphs-compare-${nextCompareId++}`;

      const item = document.createElement('div');
      item.className = 'time-series-graphs__compare-item';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = checkboxId;
      checkbox.value = flight.filename;
      checkbox.className = 'time-series-graphs__compare-checkbox';
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          this.selectedCompareFilenames.add(flight.filename);
        } else {
          this.selectedCompareFilenames.delete(flight.filename);
        }
        this.onCompareSelectionChange();
      });

      const swatch = createSwatchButton(
        'time-series-graphs__compare-swatch',
        flight.name,
        this.flightColor(flight.filename),
      );
      swatch.addEventListener('click', () =>
        toggleColorPopover(swatch, {
          title: flight.name,
          color: this.flightColor(flight.filename),
          isCustom: this.colors.isCustom(flight.filename),
          onPick: (hex) => this.colors.set(flight.filename, hex),
          onReset: () => this.colors.reset(flight.filename),
        }),
      );
      this.swatches.set(flight.filename, swatch);

      const label = document.createElement('label');
      label.htmlFor = checkboxId;
      label.textContent = flight.name;

      item.appendChild(checkbox);
      item.appendChild(swatch);
      item.appendChild(label);
      this.compareContainer.appendChild(item);
    }
  }

  /**
   * Handle change on the checkboxes: load/unload comparison data as needed.
   */
  private async onCompareSelectionChange(): Promise<void> {
    // Remove comparisons that are no longer selected
    this.loadedComparisons = this.loadedComparisons.filter(
      (c) => this.selectedCompareFilenames.has(c.filename),
    );

    // Add newly selected comparisons: cached ones right away, the rest by
    // starting a fetch (skipping ones already in flight)
    const alreadyLoaded = new Set(this.loadedComparisons.map((c) => c.filename));
    for (const filename of this.selectedCompareFilenames) {
      if (alreadyLoaded.has(filename)) continue;
      const cached = this.flightCache.get(filename);
      if (cached) {
        this.addComparison(filename, cached);
      } else if (!this.pendingLoads.has(filename)) {
        this.pendingLoads.set(filename, this.loadComparison(filename));
      }
    }

    // Refresh chart (use empty store if no live data yet) so removals show immediately
    this.hooks.refresh();

    if (this.pendingLoads.size === 0) return;

    // Keep the loading overlay up until every fetch has settled, including
    // fetches started by later selection changes while we were waiting.
    this.hooks.setLoading(true);
    while (this.pendingLoads.size > 0) {
      await Promise.all(this.pendingLoads.values());
    }

    this.hooks.refresh();
    this.hooks.setLoading(false);
  }

  /**
   * Fetch one comparison flight and add it to the chart data if it is still
   * selected when the fetch completes. Never rejects.
   */
  private async loadComparison(filename: string): Promise<void> {
    try {
      const data = await fetchPreviousFlightData(filename);
      if (data) {
        this.flightCache.set(filename, data);
        if (this.selectedCompareFilenames.has(filename)) {
          this.addComparison(filename, data);
        }
      }
    } catch (err) {
      logger.error('Failed to load comparison flight', { filename, error: String(err) });
    } finally {
      this.pendingLoads.delete(filename);
    }
  }

  /** Add a comparison flight to the chart data unless it is already there. */
  private addComparison(filename: string, flight: PreviousFlightData): void {
    if (this.loadedComparisons.some((c) => c.filename === filename)) return;
    const info = this.previousFlights.find((f) => f.filename === filename);
    this.loadedComparisons.push({
      filename,
      name: info?.name ?? filename,
      data: flight.store,
      engineEvents: flight.engineEvents,
      separationMetSeconds: flight.separationMetSeconds,
    });
  }
}
