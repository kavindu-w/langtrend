import { Calendar } from '@fullcalendar/core';
import dayGridPlugin from '@fullcalendar/daygrid';

// ── Pure helpers (no DOM) ────────────────────────────────────────────────────

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function startOfWeek(d) {
  const copy = new Date(d);
  const day = (copy.getDay() + 6) % 7; // Monday = 0
  copy.setDate(copy.getDate() - day);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function isoWeekNumber(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
}

function fmtShort(d) { return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }

function fmtTitle(start) {
  const end = new Date(start); end.setDate(end.getDate() + 6);
  const sm = start.toLocaleDateString(undefined, { month: 'long' });
  const em = end.toLocaleDateString(undefined, { month: 'long' });
  const year = end.getFullYear();
  if (sm === em) return `The Week of ${sm} ${start.getDate()}–${end.getDate()}, ${year}`;
  return `The Week of ${sm} ${start.getDate()}–${em} ${end.getDate()}, ${year}`;
}

function buildWeekLabel(slug) {
  const start = new Date(slug + 'T12:00:00');
  const end = new Date(start); end.setDate(end.getDate() + 6);
  return `${fmtShort(start)} – ${fmtShort(end)}`;
}

// ── Panel / week data ────────────────────────────────────────────────────────

const panel = document.querySelector('.week-calendar-panel');
const availableWeeks = JSON.parse(panel?.dataset.availableWeeks || '[]');
const baseUrl = (panel?.dataset.baseUrl || '').replace(/\/+$/, '');

const earliestAvailable = availableWeeks.length
  ? new Date(availableWeeks[0] + 'T12:00:00')
  : new Date(2026, 3, 27);
const latestAvailable = availableWeeks.length
  ? new Date(availableWeeks[availableWeeks.length - 1] + 'T12:00:00')
  : new Date();
const earliestStr = isoDate(earliestAvailable);
const latestStr = isoDate(latestAvailable);

// ── Determine anchor week (before mode check to prevent flash) ───────────────

let anchorWeekStr = '';
{
  const serverWeek = panel?.dataset.activeWeek;
  let start = serverWeek ? startOfWeek(new Date(serverWeek + 'T12:00:00')) : null;
  if (!start || isNaN(start) || isoDate(start) < earliestStr) start = startOfWeek(latestAvailable);
  anchorWeekStr = isoDate(start);
}

// ── Determine initial view mode from URL ─────────────────────────────────────

const _ip = new URLSearchParams(location.search);
let viewMode = _ip.get('period') === 'all' ? 'all' : (_ip.get('from') ? 'period' : 'week');

// Apply data-mode before calendar renders so CSS hides the widget immediately in all-papers mode
if (panel) panel.dataset.mode = viewMode;

// ── DOM refs ─────────────────────────────────────────────────────────────────

const root = document.getElementById('fc-root');
const prevBtn = document.getElementById('fc-prev');
const nextBtn = document.getElementById('fc-next');
const titleEl = document.getElementById('week-title');
const periodControls = document.getElementById('period-controls');
const fromSelect = document.getElementById('period-from-select');
const toSelect = document.getElementById('period-to-select');

// ── Toast ────────────────────────────────────────────────────────────────────

function showToast(msg, timeout = 3000) {
  const t = document.createElement('div');
  t.className = 'lt-toast';
  t.setAttribute('role', 'status');
  t.setAttribute('aria-live', 'polite');
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('visible'));
  setTimeout(() => { t.classList.remove('visible'); setTimeout(() => t.remove(), 300); }, timeout);
}

// ── URL helpers ──────────────────────────────────────────────────────────────

function buildParams(overrides) {
  const p = new URLSearchParams(location.search);
  if (overrides) {
    for (const [k, v] of Object.entries(overrides)) {
      if (v === null || v === undefined) p.delete(k); else p.set(k, String(v));
    }
  }
  return p;
}

function navTo(weekSlug, paramOverrides) {
  const p = buildParams(paramOverrides);
  const qs = p.toString();
  window.location.href = `${baseUrl}/weeks/${weekSlug}/${qs ? '?' + qs : ''}`;
}

function getForwardedParams() {
  const p = new URLSearchParams(location.search);
  const parts = [];
  const lang = p.get('lang'); if (lang) parts.push(`lang=${encodeURIComponent(lang)}`);
  const from = p.get('from'); if (from) parts.push(`from=${encodeURIComponent(from)}`);
  const period = p.get('period'); if (period) parts.push(`period=${encodeURIComponent(period)}`);
  return parts.length ? '?' + parts.join('&') : '';
}

// ── Period selects ────────────────────────────────────────────────────────────

function populatePeriodSelects() {
  const fromYearSel  = document.getElementById('from-year-select');
  const fromMonthSel = document.getElementById('from-month-select');
  const toYearSel    = document.getElementById('to-year-select');
  const toMonthSel   = document.getElementById('to-month-select');
  if (!fromSelect || !toSelect || !fromYearSel || !fromMonthSel || !toYearSel || !toMonthSel) return;

  const data = buildWeekData();
  const params = new URLSearchParams(location.search);
  const fromParam = params.get('from') || anchorWeekStr;

  const fromD = new Date(fromParam + 'T12:00:00');
  const toD   = new Date(anchorWeekStr + 'T12:00:00');

  // Initialise From cascade
  fillYearSelect(fromYearSel, data, fromD.getFullYear());
  fillMonthSelect(fromMonthSel, data, fromD.getFullYear(), fromD.getMonth() + 1);
  fillWeekSelect(fromSelect, data, fromD.getFullYear(), fromD.getMonth() + 1, fromParam);

  // Initialise To cascade
  fillYearSelect(toYearSel, data, toD.getFullYear());
  fillMonthSelect(toMonthSel, data, toD.getFullYear(), toD.getMonth() + 1);
  fillWeekSelect(toSelect, data, toD.getFullYear(), toD.getMonth() + 1, anchorWeekStr);

  // From cascading (year/month filter only; week triggers navigation)
  fromYearSel.addEventListener('change', () => {
    fillMonthSelect(fromMonthSel, data, parseInt(fromYearSel.value), null);
    fillWeekSelect(fromSelect, data, parseInt(fromYearSel.value), parseInt(fromMonthSel.value), null);
  });
  fromMonthSel.addEventListener('change', () => {
    fillWeekSelect(fromSelect, data, parseInt(fromYearSel.value), parseInt(fromMonthSel.value), null);
  });
  fromSelect.addEventListener('change', onPeriodSelectChange);

  // To cascading
  toYearSel.addEventListener('change', () => {
    fillMonthSelect(toMonthSel, data, parseInt(toYearSel.value), null);
    fillWeekSelect(toSelect, data, parseInt(toYearSel.value), parseInt(toMonthSel.value), null);
  });
  toMonthSel.addEventListener('change', () => {
    fillWeekSelect(toSelect, data, parseInt(toYearSel.value), parseInt(toMonthSel.value), null);
  });
  toSelect.addEventListener('change', onPeriodSelectChange);
}

// Build year→month→weeks lookup from availableWeeks (shared by jump and period selects)
function buildWeekData() {
  const data = new Map(); // year → Map(month → [slugs])
  for (const slug of availableWeeks) {
    const d = new Date(slug + 'T12:00:00');
    const y = d.getFullYear(), m = d.getMonth() + 1;
    if (!data.has(y)) data.set(y, new Map());
    if (!data.get(y).has(m)) data.get(y).set(m, []);
    data.get(y).get(m).push(slug);
  }
  return data;
}

function fillYearSelect(select, data, selectedYear) {
  select.innerHTML = '';
  for (const year of [...data.keys()].sort((a, b) => a - b)) {
    const opt = document.createElement('option');
    opt.value = year; opt.textContent = year;
    if (year === selectedYear) opt.selected = true;
    select.appendChild(opt);
  }
}

function fillMonthSelect(select, data, year, selectedMonth) {
  const months = [...(data.get(year)?.keys() || [])].sort((a, b) => a - b);
  select.innerHTML = '';
  for (const m of months) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = new Date(year, m - 1, 1).toLocaleDateString(undefined, { month: 'long' });
    if (m === selectedMonth) opt.selected = true;
    select.appendChild(opt);
  }
  return months[0] ?? null; // return first month if none selected
}

function fillWeekSelect(select, data, year, month, selectedSlug) {
  const slugs = data.get(year)?.get(month) || [];
  select.innerHTML = '';
  for (const slug of slugs) {
    const opt = document.createElement('option');
    opt.value = slug; opt.textContent = buildWeekLabel(slug);
    if (slug === selectedSlug) opt.selected = true;
    select.appendChild(opt);
  }
  return slugs[0] ?? null;
}

function populateCalendarJump() {
  const yearSel = document.getElementById('calendar-year-jump');
  const monthSel = document.getElementById('calendar-month-jump');
  if (!yearSel || !monthSel) return;

  const data = buildWeekData();
  const anchorD = new Date(anchorWeekStr + 'T12:00:00');
  const curY = anchorD.getFullYear(), curM = anchorD.getMonth() + 1;

  fillYearSelect(yearSel, data, curY);
  fillMonthSelect(monthSel, data, curY, curM);

  function jumpCalendar() {
    calendar.gotoDate(new Date(parseInt(yearSel.value), parseInt(monthSel.value) - 1, 1));
  }
  yearSel.addEventListener('change', () => {
    fillMonthSelect(monthSel, data, parseInt(yearSel.value), null);
    jumpCalendar();
  });
  monthSel.addEventListener('change', jumpCalendar);
}

function onPeriodSelectChange() {
  if (!fromSelect || !toSelect) return;
  let from = fromSelect.value;
  let to = toSelect.value;
  if (from > to) { const tmp = from; from = to; to = tmp; } // ensure from ≤ to
  navTo(to, { from, period: null });
}

// ── Mode management ──────────────────────────────────────────────────────────

function updateModeTabs() {
  document.querySelectorAll('.mode-tab').forEach(tab => {
    const active = tab.dataset.mode === viewMode;
    tab.setAttribute('aria-selected', String(active));
    tab.classList.toggle('active', active);
  });
  if (panel) panel.dataset.mode = viewMode;

  // Period controls visible only in period mode
  if (periodControls) periodControls.style.display = viewMode === 'period' ? '' : 'none';

  // Calendar jump visible in week mode only (period has its own year/month selects)
  const jumpRow = document.getElementById('calendar-month-jump-row');
  if (jumpRow) jumpRow.style.display = viewMode === 'week' ? '' : 'none';

  // Prev/next disabled in all-papers mode
  const inAll = viewMode === 'all';
  if (prevBtn) { prevBtn.setAttribute('aria-disabled', String(inAll)); prevBtn.style.opacity = inAll ? '0.35' : ''; }
  if (nextBtn) { nextBtn.setAttribute('aria-disabled', String(inAll)); nextBtn.style.opacity = inAll ? '0.35' : ''; }
}

document.querySelectorAll('.mode-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const mode = tab.dataset.mode;
    if (mode === viewMode && mode !== 'period') return; // clicking current non-period tab is a no-op

    if (mode === 'week') {
      navTo(anchorWeekStr, { from: null, period: null });
    } else if (mode === 'all') {
      navTo(anchorWeekStr, { from: null, period: 'all' });
    } else if (mode === 'period') {
      viewMode = 'period';
      updateModeTabs();
      // Drop period=all from URL silently (keep lang and from if present)
      const p = buildParams({ period: null });
      history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p.toString() : ''));
      updateTitle();
      initRangeHighlight();
      populatePeriodSelects();
    }
  });
});

// Period select listeners are added inside populatePeriodSelects()

// ── FullCalendar ─────────────────────────────────────────────────────────────

function renderWeekNumbers() {
  root.querySelectorAll('.fc-wknum').forEach(el => el.remove());
  const rootRect = root.getBoundingClientRect();
  root.querySelectorAll('tr').forEach(row => {
    const firstCell = row.querySelector('td.fc-daygrid-day[data-date]');
    if (!firstCell) return;
    const date = new Date(firstCell.getAttribute('data-date') + 'T12:00:00');
    const cellRect = firstCell.getBoundingClientRect();
    const badge = document.createElement('span');
    badge.className = 'fc-wknum';
    badge.textContent = isoWeekNumber(date);
    badge.style.top = Math.round(cellRect.top - rootRect.top + cellRect.height / 2) + 'px';
    root.appendChild(badge);
  });
}

const calendar = new Calendar(root, {
  plugins: [dayGridPlugin],
  initialView: 'dayGridMonth',
  headerToolbar: false,
  firstDay: 1,
  height: 'auto',
  datesSet: (info) => {
    renderWeekNumbers();
    // Sync week-mode jump selects with the displayed calendar month
    const d = info.view.currentStart;
    const yearSel = document.getElementById('calendar-year-jump');
    const monthSel = document.getElementById('calendar-month-jump');
    if (yearSel) yearSel.value = d.getFullYear();
    if (monthSel) monthSel.value = d.getMonth() + 1;
  },
  dayCellClassNames: (arg) => {
    const weekStartStr = isoDate(startOfWeek(arg.date));
    if (weekStartStr < earliestStr || weekStartStr > latestStr) return ['unavailable-week'];
    return [];
  },
});

calendar.render();
window.addEventListener('resize', () => requestAnimationFrame(renderWeekNumbers));

// Populate and wire up the year/month jump selects (listeners added inside)
populateCalendarJump();

// ── Calendar click ────────────────────────────────────────────────────────────

root.addEventListener('click', (e) => {
  const row = e.target.closest('tr');
  if (!row) return;
  const firstCell = row.querySelector('td.fc-daygrid-day[data-date]');
  if (!firstCell) return;
  const start = startOfWeek(new Date(firstCell.getAttribute('data-date') + 'T12:00:00'));
  const startStr = isoDate(start);
  if (startStr < earliestStr) { showToast(`Data is only available from ${fmtShort(earliestAvailable)}.`); return; }
  if (startStr > latestStr) { showToast('No data available for future weeks.'); return; }

  if (viewMode === 'week') {
    // Navigate to that week, clearing any range params
    setSelectedWeek(start);
    navTo(startStr, { from: null, period: null });
  } else if (viewMode === 'period') {
    // Clicking a week sets the "To" end of the range; ?from= is preserved
    setSelectedWeek(start);
    navTo(startStr, { period: null }); // keeps ?from= and ?lang= via buildParams
  } else if (viewMode === 'all') {
    // Clicking a week in all-papers mode switches to week view
    navTo(startStr, { from: null, period: null });
  }
});

// ── Week highlight ────────────────────────────────────────────────────────────

function setSelectedWeek(start) {
  const existing = calendar.getEventById('selected-week-bg');
  if (existing) existing.remove();
  const end = new Date(start); end.setDate(end.getDate() + 7);
  calendar.addEvent({
    id: 'selected-week-bg',
    start: isoDate(start),
    end: isoDate(end),
    display: 'background',
    backgroundColor: 'rgba(15,108,93,0.12)',
    classNames: ['selected-week-bg'],
  });
}

// ── Range highlight ───────────────────────────────────────────────────────────

function clearRangeEvents() {
  calendar.getEvents().filter(e => e.id.startsWith('range-week-')).forEach(e => e.remove());
}

function applyRangeHighlight(activeWeeks) {
  clearRangeEvents();
  activeWeeks.filter(w => w !== anchorWeekStr).forEach((weekStart, i) => {
    const s = new Date(weekStart + 'T12:00:00');
    const e = new Date(s); e.setDate(e.getDate() + 7);
    calendar.addEvent({
      id: `range-week-${i}`,
      start: weekStart,
      end: isoDate(e),
      display: 'background',
      backgroundColor: 'rgba(15,108,93,0.09)',
      classNames: ['range-week-bg'],
    });
  });
}

function initRangeHighlight() {
  clearRangeEvents();
  const params = new URLSearchParams(location.search);
  const fromParam = params.get('from');
  const periodParam = params.get('period');
  if (periodParam === 'all') {
    applyRangeHighlight(availableWeeks);
  } else if (fromParam) {
    const fromIdx = availableWeeks.indexOf(fromParam);
    const anchorIdx = availableWeeks.indexOf(anchorWeekStr);
    if (fromIdx !== -1 && anchorIdx !== -1) {
      applyRangeHighlight(availableWeeks.slice(
        Math.min(fromIdx, anchorIdx),
        Math.max(fromIdx, anchorIdx) + 1,
      ));
    }
  }
}

// ── Title ─────────────────────────────────────────────────────────────────────

function updateTitle() {
  const params = new URLSearchParams(location.search);
  const fromParam = params.get('from');
  const anchorDate = new Date(anchorWeekStr + 'T12:00:00');
  let text;

  if (viewMode === 'all') {
    text = 'All papers';
  } else if (viewMode === 'period' && fromParam) {
    const fromDate = new Date(fromParam + 'T12:00:00');
    const anchorEnd = new Date(anchorDate); anchorEnd.setDate(anchorEnd.getDate() + 6);
    const fromYear = fromDate.getFullYear();
    const toYear = anchorEnd.getFullYear();
    if (fromYear === toYear) {
      text = `${fmtShort(fromDate)} – ${fmtShort(anchorEnd)}, ${toYear}`;
    } else {
      text = `${fmtShort(fromDate)} ${fromYear} – ${fmtShort(anchorEnd)} ${toYear}`;
    }
  } else {
    text = fmtTitle(anchorDate);
  }

  if (titleEl) titleEl.textContent = text;
  // Stat cards always reflect the anchor (most recent) week
  const statsTitle = document.getElementById('weekly-stat-title');
  if (statsTitle) statsTitle.textContent = `For ${fmtTitle(anchorDate)}`;
}

// ── Nav boundary ──────────────────────────────────────────────────────────────

function updateNavBoundaryState() {
  if (viewMode === 'all') return;
  const anchorD = new Date(anchorWeekStr + 'T12:00:00');
  const pStr = isoDate(new Date(anchorD.getTime() - 7 * 86400000));
  const nStr = isoDate(new Date(anchorD.getTime() + 7 * 86400000));
  const atStart = pStr < earliestStr;
  const atEnd   = nStr > latestStr;
  if (prevBtn) { prevBtn.setAttribute('aria-disabled', atStart ? 'true' : 'false'); prevBtn.style.opacity = atStart ? '0.45' : ''; }
  if (nextBtn) { nextBtn.setAttribute('aria-disabled', atEnd   ? 'true' : 'false'); nextBtn.style.opacity = atEnd   ? '0.45' : ''; }
}

// ── Prev / Next ───────────────────────────────────────────────────────────────

prevBtn?.addEventListener('click', () => {
  if (viewMode === 'all') return;
  const anchorD = new Date(anchorWeekStr + 'T12:00:00');
  const prevStart = isoDate(new Date(anchorD.getTime() - 7 * 86400000));
  if (prevStart < earliestStr) { showToast(`Data is only available from ${fmtShort(earliestAvailable)}.`); return; }
  window.location.href = `${baseUrl}/weeks/${prevStart}/${getForwardedParams()}`;
});

nextBtn?.addEventListener('click', () => {
  if (viewMode === 'all') return;
  const anchorD = new Date(anchorWeekStr + 'T12:00:00');
  const nextStart = isoDate(new Date(anchorD.getTime() + 7 * 86400000));
  if (nextStart > latestStr) { showToast('No data available for future weeks.'); return; }
  window.location.href = `${baseUrl}/weeks/${nextStart}/${getForwardedParams()}`;
});

// ── Init ──────────────────────────────────────────────────────────────────────

{
  const anchorDate = new Date(anchorWeekStr + 'T12:00:00');
  calendar.gotoDate(anchorDate);
  setSelectedWeek(anchorDate);
  updateModeTabs();
  updateTitle();
  initRangeHighlight();
  updateNavBoundaryState();
  if (prevBtn) prevBtn.setAttribute('data-tooltip', 'Previous week');
  if (nextBtn) nextBtn.setAttribute('data-tooltip', 'Next week');

  // Populate period selects if starting in period mode
  if (viewMode === 'period') populatePeriodSelects();
}
