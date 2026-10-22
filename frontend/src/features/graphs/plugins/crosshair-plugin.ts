import type { Chart } from 'chart.js/auto';

/**
 * Custom Chart.js plugin that draws a vertical crosshair line at the mouse position.
 */
export const crosshairPlugin = {
  id: 'crosshair',
  afterDraw(chart: Chart) {
    const tooltip = chart.tooltip;
    if (!tooltip || !tooltip.opacity) return;

    const ctx = chart.ctx;
    const x = tooltip.caretX;
    const topY = chart.scales.y.top;
    const bottomY = chart.scales.y.bottom;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, topY);
    ctx.lineTo(x, bottomY);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.restore();
  },
};
