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

/** Watermark size and inset from the bottom-right corner, in CSS pixels. */
const WATERMARK_FONT_PX = 12;
const WATERMARK_MARGIN_PX = 10;

/**
 * Draw a chart canvas onto a new opaque canvas and stamp the watermark.
 *
 * The chart canvas itself is transparent (the panel colour comes from CSS),
 * so the snapshot is composited onto an opaque background first; otherwise
 * the grey axis text would be unreadable in most image viewers. The copy is
 * made at the canvas's backing resolution, so high-DPI screens export sharp,
 * and the watermark is scaled by the same ratio so it has the same visual
 * size on every screen.
 *
 * The watermark sits in the bottom-right corner, level with the centred
 * x-axis title and below the tick labels, so it never covers plotted data.
 */
export function renderSnapshot(source: HTMLCanvasElement, background: string): HTMLCanvasElement | null {
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext('2d');
  if (!ctx) return null;

  ctx.fillStyle = background;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(source, 0, 0);

  const scale = source.clientWidth > 0 ? source.width / source.clientWidth : 1;
  ctx.font = `${WATERMARK_FONT_PX * scale}px 'JetBrains Mono', monospace`;
  ctx.fillStyle = 'rgba(153, 153, 153, 0.7)';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'bottom';
  ctx.fillText(
    WATERMARK_TEXT,
    out.width - WATERMARK_MARGIN_PX * scale,
    out.height - WATERMARK_MARGIN_PX * scale,
  );

  return out;
}

/**
 * Save a chart canvas as a watermarked PNG download.
 */
export function saveCanvasAsPng(source: HTMLCanvasElement, filename: string, background: string): void {
  const out = renderSnapshot(source, background);
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
