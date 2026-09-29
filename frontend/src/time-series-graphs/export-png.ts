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

/**
 * Save a canvas as a PNG download.
 *
 * The chart canvas itself is transparent (the panel colour comes from CSS),
 * so the snapshot is composited onto an opaque background first; otherwise
 * the grey axis text would be unreadable in most image viewers. The copy is
 * made at the canvas's backing resolution, so high-DPI screens export sharp.
 */
export function saveCanvasAsPng(source: HTMLCanvasElement, filename: string, background: string): void {
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext('2d');
  if (!ctx) return;

  ctx.fillStyle = background;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(source, 0, 0);

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
