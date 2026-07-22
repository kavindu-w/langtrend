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
      // Summed per language/class across weeks in this period; "studied" also
      // absorbs not-yet-judged counts (item.studied already reflects that
      // rule from build_snapshot_manifest / countLanguages), "mentioned"
      // tracks confirmed mentioned_only-only sightings.
      languageStudied: new Map(),
      languageMentioned: new Map(),
      classStudied: new Map(),
      classMentioned: new Map(),
    };

    existing.sortKey = existing.sortKey < week.weekStart ? existing.sortKey : week.weekStart;
    existing.papers += week.papers || 0;
    existing.flaggedPapers += week.flaggedPapers || 0;
    for (const item of week.languageCounts || []) {
      if (!item.language) continue;
      const studied = item.studied ?? item.count ?? 0;
      const mentioned = item.mentioned_only ?? 0;
      if (studied) existing.languageStudied.set(item.language, (existing.languageStudied.get(item.language) || 0) + studied);
      if (mentioned) existing.languageMentioned.set(item.language, (existing.languageMentioned.get(item.language) || 0) + mentioned);
    }
    for (const item of week.classCounts || []) {
      const studied = item.studied ?? item.count ?? 0;
      const mentioned = item.mentioned_only ?? 0;
      if (studied) existing.classStudied.set(item.class_id, (existing.classStudied.get(item.class_id) || 0) + studied);
      if (mentioned) existing.classMentioned.set(item.class_id, (existing.classMentioned.get(item.class_id) || 0) + mentioned);
    }
    groups.set(periodKey, existing);
  }

  return [...groups.values()]
    .sort((left, right) => left.sortKey.localeCompare(right.sortKey))
    .map((group) => {
      const allLanguages = new Set([...group.languageStudied.keys(), ...group.languageMentioned.keys()]);
      const languageCounts = [...allLanguages]
        .map((language) => {
          const studied = group.languageStudied.get(language) || 0;
          const mentioned_only = group.languageMentioned.get(language) || 0;
          return { language, count: studied + mentioned_only, studied, mentioned_only };
        })
        .sort((left, right) => right.count - left.count || left.language.localeCompare(right.language));
      const allClasses = new Set([...group.classStudied.keys(), ...group.classMentioned.keys()]);
      const classCounts = [...allClasses]
        .map((class_id) => {
          const studied = group.classStudied.get(class_id) || 0;
          const mentioned_only = group.classMentioned.get(class_id) || 0;
          return { class_id, count: studied + mentioned_only, studied, mentioned_only };
        })
        .sort((left, right) => right.count - left.count || left.class_id - right.class_id);
      // A language counts toward the studied headline if it has any studied
      // (or not-yet-judged) mentions anywhere in the period; mentioned-only
      // is the complementary set that's never studied in the period at all.
      const uniqueLanguages = languageCounts.filter((item) => item.studied > 0).length;
      const uniqueLanguagesMentionedOnly = languageCounts.filter((item) => item.studied === 0 && item.mentioned_only > 0).length;

      return {
        periodKey: group.periodKey,
        label: group.label,
        sortKey: group.sortKey,
        papers: group.papers,
        flaggedPapers: group.flaggedPapers,
        uniqueLanguages,
        uniqueLanguagesMentionedOnly,
        languageCounts,
        classCounts,
      };
    });
}

export function buildBumpSegmentPath(start, end) {
  const midX = (start.x + end.x) / 2;
  return `M ${start.x} ${start.y} C ${midX} ${start.y} ${midX} ${end.y} ${end.x} ${end.y}`;
}

// Leader line from a bump-chart point that sits at the first/last plotted column
// out to its margin label. The whole line lives in the empty margin, so a gentle
// bow is enough to keep it from tracing directly along a rank gridline.
export function buildBumpEdgeLeaderPath(x1, y1, x2, y2) {
  const midX = (x1 + x2) / 2;
  const bow = 5;
  return `M ${x1} ${y1} C ${midX} ${y1 - bow} ${midX} ${y2 - bow} ${x2} ${y2}`;
}

// Chooses which inter-row gap a mid-chart entry/exit's leader line should jog
// through: the gap between this point's rank row and the next one, either above
// or below — every column shares the same rank axis, so that gap is circle-free
// all the way across. Picks whichever side is closer to the label, falling back
// to the only available side at the rank-1/rank-max edges of the axis.
export function computeBumpFlyoverLaneY(y1, y2, rowSpacing, plotHeight) {
  const halfGap = rowSpacing / 2;
  const canGoAbove = y1 - halfGap > 0.01;
  const canGoBelow = y1 + halfGap < plotHeight - 0.01;
  const goAbove = canGoAbove && (!canGoBelow || y2 <= y1);
  return goAbove ? y1 - halfGap : y1 + halfGap;
}

// Multiple mid-chart entries/exits can land on the exact same inter-row gap (e.g.
// two languages both jogging through the rank 7/8 gap toward the same margin) —
// including cases where one heads to the left margin and the other to the right,
// since both still travel the full width of that shared lane. Pass the left- and
// right-side lane Ys combined into one array (see callers) so the whole shared
// lane is deconflicted together, not per side. Groups lane Ys that round to the
// same pixel and fans each group out by `step` so parallel leaders stay apart.
export function assignBumpLaneOffsets(laneYs, step = 7) {
  const groups = new Map();
  laneYs.forEach((y, idx) => {
    if (y == null) return;
    const key = Math.round(y);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(idx);
  });
  const offsets = new Array(laneYs.length).fill(0);
  groups.forEach((idxs) => {
    const n = idxs.length;
    idxs.forEach((idx, i) => { offsets[idx] = (i - (n - 1) / 2) * step; });
  });
  return offsets;
}

// Leader line from a bump-chart point that enters/exits the top-10 mid-chart (not
// at the first/last column) out to its margin label. A straight line would cut
// across the intervening columns' circles and curves, so instead it jogs into a
// circle-free inter-row lane (see computeBumpFlyoverLaneY/assignBumpLaneOffsets)
// and travels through it to the margin before turning toward the label.
export function buildBumpFlyoverLeaderPath(x1, y1, x2, y2, laneY) {
  const r = Math.min(8, Math.abs(laneY - y1) / 2, Math.abs(x2 - x1) / 2);
  const dx = x2 >= x1 ? 1 : -1;
  const dy1 = laneY >= y1 ? 1 : -1;
  const dy2 = y2 >= laneY ? 1 : -1;
  return [
    `M ${x1} ${y1}`,
    `L ${x1} ${laneY - dy1 * r}`,
    `Q ${x1} ${laneY} ${x1 + dx * r} ${laneY}`,
    `L ${x2 - dx * r} ${laneY}`,
    `Q ${x2} ${laneY} ${x2} ${laneY + dy2 * r}`,
    `L ${x2} ${y2}`,
  ].join(' ');
}

export function scrollableChartWidth(baseWidth, margins, periodCount, maxVisible = 5) {
  if (periodCount <= maxVisible) return null;
  const baseInner = baseWidth - margins.left - margins.right;
  const pxPerPeriod = baseInner / maxVisible;
  return Math.ceil(periodCount * pxPerPeriod) + margins.left + margins.right;
}

export function coverageSliceLines(label, value) {
  if (label === 'Papers with detected languages') {
    return ['Papers with', `detected languages (${value})`];
  }
  return [`${label} (${value})`];
}

export function coverageSliceLinesWithPercent(label, value, total) {
  const percentLabel = total > 0 ? ` (${formatPercent(value, total)})` : '';
  if (label === 'Papers with detected languages') {
    return ['Papers with', `detected languages (${value})${percentLabel}`];
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
    case 5: return `M ${cx - s},${cy} H ${cx + s} M ${cx},${cy - s} V ${cy + s}`; // plus/cross
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

/**
 * Rank-trajectory summary stats, computed from every language's per-week
 * studied-count series (0 where absent) across the window — not just the
 * languages that happen to be drawn in the chart's visible top 10. That's
 * deliberate: "fastest rising", "tier leader", and "diversity" all describe
 * the whole tier, and a real low-resource language growing fastest may never
 * crack the top 10 that's plotted — the exact case this project's equity
 * framing cares about, so it must still be eligible to win.
 *
 * @param {Map<string, number[]>} weeklyCounts language -> per-week studied count array (length weekCount, 0 where absent)
 * @param {number} weekCount
 * @returns {{
 *   rising: { language: string, delta: number, firstAvg: number, secondAvg: number } | null,
 *   leader: { language: string, share: number } | null,
 *   diversityEffective: number,
 * }}
 */
export function computeRankTrajectoryStats(weeklyCounts, weekCount) {
  const mid = Math.floor(weekCount / 2);
  const firstWeeks = mid;               // weeks [0, mid)
  const secondWeeks = weekCount - mid;  // weeks [mid, weekCount)
  const weeklyTotals = new Array(weekCount).fill(0);
  weeklyCounts.forEach((counts) => {
    for (let i = 0; i < weekCount; i++) weeklyTotals[i] += counts[i] || 0;
  });

  // Fastest rising: largest gain in average share, later half vs earlier half.
  let rising = null;
  weeklyCounts.forEach((counts, language) => {
    let firstShare = 0;
    let secondShare = 0;
    for (let i = 0; i < weekCount; i++) {
      const share = weeklyTotals[i] > 0 ? (counts[i] || 0) / weeklyTotals[i] : 0;
      if (i < mid) firstShare += share; else secondShare += share;
    }
    const firstAvg = firstWeeks ? firstShare / firstWeeks : 0;
    const secondAvg = secondWeeks ? secondShare / secondWeeks : 0;
    const delta = secondAvg - firstAvg;
    if (delta <= 0) return;
    if (!rising || delta > rising.delta || (delta === rising.delta && secondAvg > rising.secondAvg)) {
      rising = { language, delta, firstAvg, secondAvg };
    }
  });

  // Tier leader + diversity: total share held across the whole window.
  const totals = new Map();
  let grandTotal = 0;
  weeklyCounts.forEach((counts, language) => {
    let total = 0;
    for (let i = 0; i < weekCount; i++) total += counts[i] || 0;
    if (total > 0) { totals.set(language, total); grandTotal += total; }
  });
  let leaderLanguage = null;
  totals.forEach((total, language) => {
    if (!leaderLanguage || total > totals.get(leaderLanguage)) leaderLanguage = language;
  });
  const leader = leaderLanguage
    ? { language: leaderLanguage, share: grandTotal > 0 ? totals.get(leaderLanguage) / grandTotal : 0 }
    : null;

  // Diversity: effective number of languages = exp(Shannon entropy) of each
  // language's total-window share. Equals the raw count when attention is
  // spread evenly, and drops well below it when a few languages dominate.
  let entropy = 0;
  if (grandTotal > 0) {
    totals.forEach((total) => {
      const p = total / grandTotal;
      if (p > 0) entropy -= p * Math.log(p);
    });
  }
  const diversityEffective = grandTotal > 0 ? Math.exp(entropy) : 0;

  return { rising, leader, diversityEffective };
}

/**
 * Formats the "effective number of languages" diversity stat for display,
 * rounding once and deriving both the text and the singular/plural check from
 * that same rounded value — so "1 effective language" is reachable and never
 * disagrees with the displayed number (e.g. a naive `.toFixed(1)` always
 * prints "1.0", which can never equal the string "1").
 *
 * @param {number} effective
 * @returns {{ value: number, text: string, isSingular: boolean }}
 */
export function formatEffectiveLanguageCount(effective) {
  const value = effective >= 10 ? Math.round(effective) : Number(effective.toFixed(1));
  return { value, text: String(value), isSingular: value === 1 };
}
