/**
 * Build a download filename for a chart snapshot, e.g.
 * "starship-speedSuperHeavy-2026-09-29_13-05-38.png" (local time).
 */
export function chartFilename(seriesKey: string, now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
  return `starship-${seriesKey}-${date}_${time}.png`;
}

/** Attribution stamped onto every exported chart image. */
export const WATERMARK_TEXT = '@sanitaravel';

/** Watermark inset from the bottom-right corner, in CSS pixels. */
const WATERMARK_MARGIN_PX = 10;

export type ExportBackground = 'dark' | 'white' | 'transparent';

/** User-selectable settings for a PNG export. */
export interface ExportOptions {
  /** Tick label size in CSS px; titles, legend and watermark scale from it. */
  fontSize: number;
  background: ExportBackground;
}

/** Tick label font sizes offered in the export menu, in CSS px. */
export const FONT_SIZE_CHOICES = [10, 12, 14, 16, 20, 24];

/** Matches the on-screen chart: 10 px ticks on the dark panel. */
export const DEFAULT_EXPORT_OPTIONS: ExportOptions = { fontSize: 10, background: 'dark' };

/** Colours for chart text, gridlines and the watermark on a given background. */
export interface ChartTheme {
  /** Image background; null leaves the PNG transparent. */
  fill: string | null;
  text: string;
  grid: string;
  watermark: string;
}

export const EXPORT_THEMES: Record<ExportBackground, ChartTheme> = {
  // Same colours as the on-screen chart panel
  dark: { fill: '#333333', text: '#999999', grid: '#444444', watermark: 'rgba(153, 153, 153, 0.7)' },
  // Darker text and light gridlines so the chart reads well on paper and slides
  white: { fill: '#FFFFFF', text: '#555555', grid: '#E0E0E0', watermark: 'rgba(85, 85, 85, 0.7)' },
  // True mid-grey text (~3.9:1 on white, ~5.3:1 on black) and a translucent grid
  // stay legible whether the image lands on a light or a dark backdrop
  transparent: { fill: null, text: '#808080', grid: 'rgba(128, 128, 128, 0.35)', watermark: 'rgba(128, 128, 128, 0.85)' },
};

/** Watermark size for a given tick font size (12 px at the default 10 px). */
export function watermarkFontPx(fontSize: number): number {
  return Math.round(fontSize * 1.2);
}

/** How the watermark and background are drawn onto a snapshot. */
export interface SnapshotStyle {
  fill: string | null;
  watermarkColor: string;
  watermarkFontPx: number;
}

/**
 * Copy a rendered chart canvas onto a new canvas, fill the background and
 * stamp the watermark.
 *
 * Chart.js canvases are transparent, so for the dark and white themes the
 * chart is composited onto an opaque fill; the transparent theme skips the
 * fill. The copy keeps the canvas's backing resolution, so high-DPI screens
 * export sharp, and the watermark is scaled by the same pixel ratio so it has
 * the same visual size on every screen.
 *
 * The watermark sits in the bottom-right corner, level with the centred
 * x-axis title and below the tick labels, so it never covers plotted data.
 */
export function renderSnapshot(
  source: HTMLCanvasElement,
  style: SnapshotStyle,
  pixelRatio: number,
): HTMLCanvasElement | null {
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext('2d');
  if (!ctx) return null;

  if (style.fill) {
    ctx.fillStyle = style.fill;
    ctx.fillRect(0, 0, out.width, out.height);
  }
  ctx.drawImage(source, 0, 0);

  ctx.font = `${style.watermarkFontPx * pixelRatio}px 'JetBrains Mono', monospace`;
  ctx.fillStyle = style.watermarkColor;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'bottom';
  ctx.fillText(
    WATERMARK_TEXT,
    out.width - WATERMARK_MARGIN_PX * pixelRatio,
    out.height - WATERMARK_MARGIN_PX * pixelRatio,
  );

  return out;
}

/**
 * Save a rendered chart canvas as a watermarked PNG download.
 */
export function saveCanvasAsPng(
  source: HTMLCanvasElement,
  filename: string,
  style: SnapshotStyle,
  pixelRatio: number,
): void {
  const out = renderSnapshot(source, style, pixelRatio);
  if (!out) return;

  out.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Give the browser a tick to start the download before releasing the URL
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }, 'image/png');
}
