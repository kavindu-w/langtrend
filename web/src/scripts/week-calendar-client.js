import { Calendar } from '@fullcalendar/core';
import dayGridPlugin from '@fullcalendar/daygrid';

const panel = document.querySelector('.week-calendar-panel');
const availableWeeks = JSON.parse(panel?.dataset.availableWeeks || '[]');
// Fallback constants in case data is empty
const _fallbackEarliest = new Date(2026, 3, 27); // Apr 27 2026
const earliestAvailable = availableWeeks.length
  ? new Date(availableWeeks[0] + 'T12:00:00')
  : _fallbackEarliest;
const latestAvailable = availableWeeks.length
  ? new Date(availableWeeks[availableWeeks.length - 1] + 'T12:00:00')
  : new Date();

// Pre-compute ISO date strings for boundary comparisons — avoids time-of-day mismatch
// (weekStart is local midnight; earliestAvailable is local noon — comparing Dates directly
// would make Apr 27 00:00 < Apr 27 12:00 = true, incorrectly darkening the first available week)
const earliestStr = isoDate(earliestAvailable);
const latestStr = isoDate(latestAvailable);

const root = document.getElementById('fc-root');
const prev = document.getElementById('fc-prev');
const next = document.getElementById('fc-next');
const title = document.getElementById('week-title');
// add accessible tooltips used by CSS
if (prev) prev.setAttribute('data-tooltip', 'Previous week');
if (next) next.setAttribute('data-tooltip', 'Next week');

// Toast helper
function showToast(msg, timeout = 3000) {
  const t = document.createElement('div');
  t.className = 'lt-toast';
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('visible'));
  setTimeout(() => {
    t.classList.remove('visible');
    setTimeout(() => t.remove(), 300);
  }, timeout);
}

function startOfWeek(d) {
  const copy = new Date(d);
  const day = (copy.getDay() + 6) % 7; // Monday=0
  copy.setDate(copy.getDate() - day);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

// Serialize in local time — avoids UTC offset shifting the date when using toISOString()
function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// ISO 8601 week number (1–53)
function isoWeekNumber(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
}

function fmtShort(d) { return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
function fmtTitle(start) {
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const sm = start.toLocaleDateString(undefined, { month: 'long' });
  const em = end.toLocaleDateString(undefined, { month: 'long' });
  if (sm === em) {
    // same month: "The Week of May 4–10"
    return `The Week of ${sm} ${start.getDate()}–${end.getDate()}`;
  }
  // cross-month: "The Week of May 26 – June 1"
  return `The Week of ${sm} ${start.getDate()}–${em} ${end.getDate()}`;
}

function updateWeekTitle(start) {
  const text = fmtTitle(start);
  if (title) title.textContent = text;
  const statsTitle = document.getElementById('weekly-stat-title');
  if (statsTitle) statsTitle.textContent = `For ${text}`;
}

// Render ISO week number badges to the left of each grid row.
// FullCalendar's native weekNumbers option positions numbers inside the grid with
// position:absolute, causing overlap with Monday cells. Instead we measure row positions
// after each render and place our own badges as absolute children of .fc-root (which has
// position:relative and margin-left that creates the necessary gutter space).
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

let bgEventId = 'selected-week-bg';

const calendar = new Calendar(root, {
  plugins: [dayGridPlugin],
  initialView: 'dayGridMonth',
  headerToolbar: false,
  firstDay: 1,
  // weekNumbers disabled — we render our own badges outside the grid via renderWeekNumbers()
  datesSet: () => renderWeekNumbers(),
  dayCellClassNames: (arg) => {
    // Compare as ISO strings to avoid time-of-day mismatch (local midnight vs noon)
    const weekStartStr = isoDate(startOfWeek(arg.date));
    if (weekStartStr < earliestStr || weekStartStr > latestStr) {
      return ['unavailable-week'];
    }
    return [];
  },
});

calendar.render();

// Re-position badges if the viewport resizes
window.addEventListener('resize', () => requestAnimationFrame(renderWeekNumbers));

// Delegated row click — fires for any click inside a week row (day cells, gaps between cells)
root.addEventListener('click', (e) => {
  const row = e.target.closest('tr');
  if (!row) return;
  const firstCell = row.querySelector('td.fc-daygrid-day[data-date]');
  if (!firstCell) return;
  const start = startOfWeek(new Date(firstCell.getAttribute('data-date') + 'T12:00:00'));
  const startStr = isoDate(start);
  if (startStr < earliestStr) {
    showToast(`Data is only available from ${fmtShort(earliestAvailable)}.`);
    return;
  }
  if (startStr > latestStr) {
    showToast('No data available for future weeks.');
    return;
  }
  setSelectedWeek(start);
  window.location.href = `?week=${startStr}`;
});

function updateUrlForWeek(start) {
  const q = new URLSearchParams(window.location.search);
  q.set('week', isoDate(start));
  history.replaceState(null, '', `${location.pathname}?${q.toString()}`);
}

function setSelectedWeek(start) {
  const existing = calendar.getEventById(bgEventId);
  if (existing) existing.remove();
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  calendar.addEvent({
    id: bgEventId,
    start: isoDate(start),
    end: isoDate(end),
    display: 'background',
    backgroundColor: 'rgba(15,108,93,0.12)',
    classNames: ['selected-week-bg']
  });
  updateWeekTitle(start);
  updateUrlForWeek(start);
}

// init selected week: prefer server-resolved activeWeek attr, then URL param, then latest available
function initSelectedWeek() {
  const serverWeek = document.querySelector('.week-calendar-panel')?.dataset.activeWeek;
  const q = new URLSearchParams(window.location.search);
  const urlWeek = q.get('week');
  const raw = serverWeek || urlWeek;
  let start;
  if (raw) {
    const parsed = new Date(raw + 'T12:00:00');
    if (!isNaN(parsed)) start = startOfWeek(parsed);
  }
  if (!start || isoDate(start) < earliestStr) start = startOfWeek(latestAvailable);
  calendar.gotoDate(start);
  setSelectedWeek(start);
}

prev.addEventListener('click', () => {
  const start = startOfWeek(calendar.getDate());
  const prevStart = new Date(start);
  prevStart.setDate(start.getDate() - 7);
  if (isoDate(prevStart) < earliestStr) {
    showToast(`Data is only available from ${fmtShort(earliestAvailable)}.`);
    return;
  }
  window.location.href = `?week=${isoDate(prevStart)}`;
});

next.addEventListener('click', () => {
  const start = startOfWeek(calendar.getDate());
  const nextStart = new Date(start);
  nextStart.setDate(start.getDate() + 7);
  if (isoDate(nextStart) > latestStr) {
    showToast('No data available for future weeks.');
    return;
  }
  window.location.href = `?week=${isoDate(nextStart)}`;
});

initSelectedWeek();
