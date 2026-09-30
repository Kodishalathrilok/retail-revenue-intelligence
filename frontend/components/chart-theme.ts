/**
 * Chart colours and axis styling, read from the token layer.
 *
 * Recharts takes colours as SVG attributes, and var() resolves in SVG
 * presentation attributes, so every chart points at app/tokens.css instead of
 * carrying its own hex values (the pages used to hard-code #0f172a, #2563eb,
 * #dc2626 and friends, which could never follow the theme).
 */

export const CHART = {
  ink: 'var(--chart-ink)',          // observed / actual
  model: 'var(--chart-model)',      // forecast, estimate
  band: 'var(--chart-band)',        // prediction / confidence interval
  context: 'var(--chart-context)',  // secondary series (raw weekly behind a trend)
  compare: 'var(--chart-compare)',  // the second arm of a comparison (treated)
  grid: 'var(--chart-grid)',
  axis: 'var(--chart-axis)',
} as const;

export const AXIS_TICK = { fontSize: 11, fill: CHART.axis } as const;

export const GRID_PROPS = { strokeDasharray: '3 3', stroke: CHART.grid } as const;

export const TOOLTIP_STYLE = {
  contentStyle: {
    background: 'var(--color-paper)',
    border: '1px solid var(--color-rule)',
    borderRadius: 'var(--radius-input)',
    fontSize: 12,
    color: 'var(--color-ink)',
  },
  labelStyle: { color: 'var(--color-muted)' },
} as const;

export const LEGEND_STYLE = { fontSize: 12, color: 'var(--color-ink-2)' } as const;
