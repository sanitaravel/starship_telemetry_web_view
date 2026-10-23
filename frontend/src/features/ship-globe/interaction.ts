import { applyDrag, MIN_ZOOM, zoomBy, type Rotation } from './geo';
import { MAX_GLOBE_SIZE, WHEEL_ZOOM_SENSITIVITY, ZOOM_STEP } from './map-data';

/** What dragging the globe reads and changes. */
export interface DragHost {
  getRotation(): Rotation;
  setRotation(rotation: Rotation): void;
  /** Globe radius in CSS pixels at the current zoom. */
  radius(): number;
  /** The user took hold of the globe: stop following the ship. */
  onGrab(): void;
  requestDraw(): void;
}

export function bindDrag(canvas: HTMLCanvasElement, host: DragHost): void {
  let dragOrigin: { x: number; y: number; rotation: Rotation } | null = null;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    dragOrigin = { x: e.clientX, y: e.clientY, rotation: host.getRotation() };
    host.onGrab();
    canvas.classList.add('ship-globe__canvas--dragging');
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!dragOrigin) return;
    const { x, y, rotation } = dragOrigin;
    // Scale by the zoomed radius so the surface tracks the pointer at any zoom.
    host.setRotation(applyDrag(rotation, e.clientX - x, e.clientY - y, host.radius()));
    host.requestDraw();
  });
  const end = (e: PointerEvent) => {
    if (!dragOrigin) return;
    dragOrigin = null;
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    canvas.classList.remove('ship-globe__canvas--dragging');
  };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
}

/** Scroll wheel and +/−/reset buttons; zoom is about the centre of the view. */
export function bindZoom(
  canvas: HTMLCanvasElement,
  container: HTMLElement,
  getZoom: () => number,
  setZoom: (zoom: number) => void,
): void {
  canvas.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      setZoom(zoomBy(getZoom(), Math.exp(-e.deltaY * WHEEL_ZOOM_SENSITIVITY)));
    },
    { passive: false },
  );
  container.querySelector('.ship-globe__zoom')!.addEventListener('click', (e) => {
    const action = (e.target as HTMLElement).closest<HTMLElement>('[data-zoom]')?.dataset.zoom;
    if (action === 'in') setZoom(zoomBy(getZoom(), ZOOM_STEP));
    else if (action === 'out') setZoom(zoomBy(getZoom(), 1 / ZOOM_STEP));
    else if (action === 'reset') setZoom(MIN_ZOOM);
  });
}

/**
 * Size the canvas to its wrapper: the column's full width so a zoomed-in
 * globe fills the area. With `fill` (overlays) the canvas takes the
 * wrapper's height too, so the globe (radius from the smaller side, centred)
 * fits whatever size the page is. Returns the new CSS size, or null if
 * nothing changed.
 */
export function fitCanvas(
  canvas: HTMLCanvasElement,
  wrap: HTMLElement,
  current: { width: number; height: number },
  fill = false,
): { width: number; height: number } | null {
  const width = Math.floor(wrap.clientWidth);
  const height = fill ? Math.floor(wrap.clientHeight) : Math.min(width, MAX_GLOBE_SIZE);
  if (width <= 0 || height <= 0 || (width === current.width && height === current.height)) return null;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  return { width, height };
}
