import { LANGUAGE_CLASS_COLORS, languageBorderClass } from './language-colors.js';

export function formatWeekRange(series, fallbackStart, fallbackEnd) {
  const startSource = series[0]?.date || fallbackStart;
  const endSource = series[series.length - 1]?.date || fallbackEnd || fallbackStart;
  if (!startSource || !endSource) {
    return 'the current window';
  }

  const start = new Date(`${startSource}T00:00:00`);
  const end = new Date(`${endSource}T00:00:00`);
  const startLabel = start.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  const endLabel = end.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  return `The Week of ${startLabel}–${endLabel}`;
}

export function formatWeekLabel(weekStart) {
  const date = new Date(`${weekStart}T00:00:00`);
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function formatWeekdayLabel(dateString) {
  const date = new Date(`${dateString}T00:00:00`);
  return date.toLocaleDateString(undefined, { weekday: 'short' });
}

export function formatPercent(value, total) {
  if (!total) return '0.0%';
  return `${((value / total) * 100).toFixed(1)}%`;
}

export function sumCounts(items) {
  return items.reduce((sum, item) => sum + (item.count || 0), 0);
}

export function buildLinePath(points) {
  if (!points.length) return '';
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' ');
}

export function classColorForId(classId) {
  return LANGUAGE_CLASS_COLORS[classId] || LANGUAGE_CLASS_COLORS[0];
}

/** @param {string} language @param {Record<string, unknown[]>} langClasses */
export function classColorForLanguage(language, langClasses) {
  const index = languageBorderClass(language, langClasses);
  return LANGUAGE_CLASS_COLORS[index] || LANGUAGE_CLASS_COLORS[0];
}

export function polarToCartesian(centerX, centerY, radius, angle) {
  return {
    x: centerX + radius * Math.cos(angle),
    y: centerY + radius * Math.sin(angle),
  };
}

export function buildPiePath(centerX, centerY, radius, startAngle, endAngle) {
  const start = polarToCartesian(centerX, centerY, radius, startAngle);
  const end = polarToCartesian(centerX, centerY, radius, endAngle);
  const largeArcFlag = endAngle - startAngle > Math.PI ? 1 : 0;
  return `M ${centerX} ${centerY} L ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 1 ${end.x} ${end.y} Z`;
}

export function buildPieSlices(items, centerX, centerY, radius, startOffset = -Math.PI / 2, labelOffset = 72) {
  const total = Math.max(1, items.reduce((sum, item) => sum + Math.max(item.value || 0, 0), 0));
  let angle = startOffset;
  const slices = [];

  for (const item of items) {
    if ((item.value || 0) <= 0) {
      continue;
    }

    const sweep = (item.value / total) * Math.PI * 2;
    const startAngle = angle;
    const endAngle = angle + sweep;
    const midAngle = startAngle + sweep / 2;
    const side = Math.cos(midAngle) >= 0 ? 'right' : 'left';
    const calloutStart = polarToCartesian(centerX, centerY, radius, midAngle);
    const calloutElbow = polarToCartesian(centerX, centerY, radius + 16, midAngle);
    const labelX = centerX + (side === 'right' ? radius + labelOffset : -(radius + labelOffset));
    const labelY = centerY + Math.sin(midAngle) * (radius + 22);

    slices.push({
      ...item,
      startAngle,
      endAngle,
      midAngle,
      path: buildPiePath(centerX, centerY, radius, startAngle, endAngle),
      calloutPath: `M ${calloutStart.x} ${calloutStart.y} L ${calloutElbow.x} ${calloutElbow.y} L ${labelX} ${labelY}`,
      labelX,
      labelY,
      side,
    });
    angle = endAngle;
  }

  const leftSlices = slices.filter((slice) => slice.side === 'left').sort((left, right) => left.labelY - right.labelY);
  const rightSlices = slices.filter((slice) => slice.side === 'right').sort((left, right) => left.labelY - right.labelY);

  const settle = (collection) => {
    let previousY = -Infinity;
    for (const slice of collection) {
      slice.labelY = Math.max(slice.labelY, previousY + 16);
      previousY = slice.labelY;
    }
  };

  settle(leftSlices);
  settle(rightSlices);
  return slices;
}

export function formatSliceLabel(label, value) {
  return `${label} (${value})`;
}

export function formatCountWithShare(count, total) {
  if (!total) return `${count}`;
  return `${count} (${Math.round((count / total) * 100)}%)`;
}

export function trendPeriodKey(weekStart, granularity) {
  if (granularity === 'week') return weekStart;
  const date = new Date(`${weekStart}T00:00:00`);
  if (granularity === 'month') {
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
  }
  return `${date.getUTCFullYear()}`;
}

export function trendPeriodLabel(weekStart, granularity) {
  const date = new Date(`${weekStart}T00:00:00`);
  if (granularity === 'week') return formatWeekLabel(weekStart);
  if (granularity === 'month') {
    return date.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
  }
  return `${date.getUTCFullYear()}`;
}

/** @param {object[]} weeks @param {'week'|'month'|'year'} granularity */
export function aggregateTrendPeriods(weeks, granularity) {
  const groups = new Map();

  for (const week of weeks) {
    const periodKey = trendPeriodKey(week.weekStart, granularity);
    const existing = groups.get(periodKey) || {
      periodKey,
      label: trendPeriodLabel(week.weekStart, granularity),
      sortKey: week.weekStart,
      papers: 0,
      flaggedPapers: 0,
      uniqueLanguages: new Set(),
      languageCounts: new Map(),
      classCounts: new Map(),
    };

    existing.sortKey = existing.sortKey < week.weekStart ? existing.sortKey : week.weekStart;
    existing.papers += week.papers || 0;
    existing.flaggedPapers += week.flaggedPapers || 0;
    for (const item of week.languageCounts || []) {
      if (!item.language) continue;
      existing.uniqueLanguages.add(item.language);
      existing.languageCounts.set(item.language, (existing.languageCounts.get(item.language) || 0) + item.count);
    }
    for (const item of week.classCounts || []) {
      existing.classCounts.set(item.class_id, (existing.classCounts.get(item.class_id) || 0) + item.count);
    }
    groups.set(periodKey, existing);
  }

  return [...groups.values()]
    .sort((left, right) => left.sortKey.localeCompare(right.sortKey))
    .map((group) => ({
      periodKey: group.periodKey,
      label: group.label,
      sortKey: group.sortKey,
      papers: group.papers,
      flaggedPapers: group.flaggedPapers,
      uniqueLanguages: group.uniqueLanguages.size,
      languageCounts: [...group.languageCounts.entries()]
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
        .map(([language, count]) => ({ language, count })),
      classCounts: [...group.classCounts.entries()]
        .sort((left, right) => right[1] - left[1] || left[0] - right[0])
        .map(([class_id, count]) => ({ class_id, count })),
    }));
}

export function buildBumpSegmentPath(start, end) {
  const midX = (start.x + end.x) / 2;
  return `M ${start.x} ${start.y} C ${midX} ${start.y} ${midX} ${end.y} ${end.x} ${end.y}`;
}

export function scrollableChartWidth(baseWidth, margins, periodCount, maxVisible = 5) {
  if (periodCount <= maxVisible) return null;
  const baseInner = baseWidth - margins.left - margins.right;
  const pxPerPeriod = baseInner / maxVisible;
  return Math.ceil(periodCount * pxPerPeriod) + margins.left + margins.right;
}

export function coverageSliceLines(label, value) {
  if (label === 'Papers with language mentions') {
    return ['Papers with', `language mentions (${value})`];
  }
  return [`${label} (${value})`];
}

export function coverageSliceLinesWithPercent(label, value, total) {
  const percentLabel = total > 0 ? ` (${formatPercent(value, total)})` : '';
  if (label === 'Papers with language mentions') {
    return ['Papers with', `language mentions (${value})${percentLabel}`];
  }
  return [`${label} (${value})${percentLabel}`];
}

export function coverageSlicePercent(value, total) {
  return formatPercent(value, total);
}

// SVG path helpers for stacked bars with flat junctions
export function roundedTopRect(x, y, w, h, r) {
  if (h <= 0) return '';
  const cr = Math.min(r, w / 2, h);
  return `M ${x + cr},${y} H ${x + w - cr} Q ${x + w},${y} ${x + w},${y + cr} V ${y + h} H ${x} V ${y + cr} Q ${x},${y} ${x + cr},${y} Z`;
}

export function roundedBottomRect(x, y, w, h, r) {
  if (h <= 0) return '';
  const cr = Math.min(r, w / 2, h);
  return `M ${x},${y} H ${x + w} V ${y + h - cr} Q ${x + w},${y + h} ${x + w - cr},${y + h} H ${x + cr} Q ${x},${y + h} ${x},${y + h - cr} V ${y} Z`;
}

// Fill Mon–Sun for the week containing weekStart.
// Uses UTC throughout to avoid local-timezone drift in toISOString().
export function fillWeekSeries(series, weekStart, _weekEnd) {
  if (!weekStart) return series;
  const map = new Map(series.map((p) => [p.date, p]));
  const [y, mo, d] = weekStart.split('-').map(Number);
  const raw = new Date(Date.UTC(y, mo - 1, d));
  const dow = raw.getUTCDay(); // 0=Sun, 1=Mon, …, 6=Sat
  // Shift to Monday: if Sunday move +1 day forward, otherwise go back to nearest Monday
  const mondayMs = dow === 0
    ? raw.getTime() + 86400000
    : raw.getTime() - (dow - 1) * 86400000;
  const filled = [];
  for (let i = 0; i < 7; i++) {
    const t = new Date(mondayMs + i * 86400000);
    const key = `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, '0')}-${String(t.getUTCDate()).padStart(2, '0')}`;
    filled.push(map.get(key) ?? { date: key, papers: 0, flagged: 0 });
  }
  return filled;
}

// Per-series SVG marker shape paths (centered at cx, cy with given size)
export function markerPath(shapeIdx, cx, cy, size) {
  const s = size;
  switch (shapeIdx % 10) {
    case 0: return `M ${cx},${cy - s} A ${s},${s} 0 1 1 ${cx - 0.001},${cy - s} Z`; // circle (approx)
    case 1: return `M ${cx - s},${cy - s} H ${cx + s} V ${cy + s} H ${cx - s} Z`; // square
    case 2: return `M ${cx},${cy - s * 1.3} L ${cx + s * 1.3},${cy} L ${cx},${cy + s * 1.3} L ${cx - s * 1.3},${cy} Z`; // diamond
    case 3: return `M ${cx},${cy - s * 1.3} L ${cx + s * 1.15},${cy + s * 0.75} L ${cx - s * 1.15},${cy + s * 0.75} Z`; // triangle up
    case 4: return `M ${cx},${cy + s * 1.3} L ${cx + s * 1.15},${cy - s * 0.75} L ${cx - s * 1.15},${cy - s * 0.75} Z`; // triangle down
    case 5: return `M ${cx - s},${cy} H ${cx + s} M ${cx},${cy - s} V ${cx},${cy + s}`.replace(`${cx},`, `${cx} `); // cross (workaround)
    case 6: { // pentagon
      const pts = Array.from({length: 5}, (_, i) => {
        const a = (i * 2 * Math.PI / 5) - Math.PI / 2;
        return `${cx + s * 1.2 * Math.cos(a)},${cy + s * 1.2 * Math.sin(a)}`;
      });
      return `M ${pts.join(' L ')} Z`;
    }
    case 7: { // hexagon
      const pts = Array.from({length: 6}, (_, i) => {
        const a = (i * Math.PI / 3);
        return `${cx + s * 1.1 * Math.cos(a)},${cy + s * 1.1 * Math.sin(a)}`;
      });
      return `M ${pts.join(' L ')} Z`;
    }
    case 8: return `M ${cx - s * 0.4},${cy - s * 1.2} H ${cx + s * 0.4} V ${cy - s * 0.4} H ${cx + s * 1.2} V ${cy + s * 0.4} H ${cx + s * 0.4} V ${cy + s * 1.2} H ${cx - s * 0.4} V ${cy + s * 0.4} H ${cx - s * 1.2} V ${cy - s * 0.4} H ${cx - s * 0.4} Z`; // plus/cross solid
    default: return `M ${cx - s},${cy - s} L ${cx + s},${cy - s} L ${cx},${cy + s} Z`; // triangle
  }
}
