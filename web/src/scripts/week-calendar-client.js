import { Calendar } from '@fullcalendar/core';
import dayGridPlugin from '@fullcalendar/daygrid';

const earliestAvailable = new Date(2026, 4, 18);
const root = document.getElementById('fc-root');
const prev = document.getElementById('fc-prev');
const next = document.getElementById('fc-next');
const title = document.getElementById('week-title');
// add accessible tooltips used by CSS
if (prev) prev.setAttribute('data-tooltip', 'Previous week');
if (next) next.setAttribute('data-tooltip', 'Next week');
// move prev/next buttons into the panel head so they sit beside the title
const panelHead = document.querySelector('.calendar-card.week-calendar-panel .panel-head');
if (panelHead && prev && next && title) {
  // insert prev before the title, and next after the title
  panelHead.insertBefore(prev, title);
  panelHead.insertBefore(next, title.nextSibling);
}

// Toast helper
function showToast(msg, timeout = 3000) {
  const t = document.createElement('div');
  t.className = 'lt-toast';
  t.textContent = msg;
  document.body.appendChild(t);
  // small entrance
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
  copy.setHours(0,0,0,0);
  return copy;
}

function fmtShort(d) { return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
function fmtShortDayMonth(d) { return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); }
function fmtTitle(start) {
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return `The Week of ${fmtShortDayMonth(start)}–${fmtShortDayMonth(end)}`;
}

function updateWeekTitle(start) {
  const text = fmtTitle(start);
  if (title) {
    title.textContent = text;
  }
  const statsTitle = document.getElementById('weekly-stat-title');
  if (statsTitle) {
    statsTitle.textContent = `For ${text}`;
  }
}

let bgEventId = 'selected-week-bg';

const calendar = new Calendar(root, {
  plugins: [dayGridPlugin],
  initialView: 'dayGridMonth',
  headerToolbar: false,
  dateClick: (info) => {
    const start = startOfWeek(info.date);
    if (start < earliestAvailable) {
      showToast('Data is only available from May 18, 2026.');
      return;
    }
    updateWeekTitle(start);
    // highlight week
    setSelectedWeek(start);
    // placeholder navigation toast
    showToast(`Would navigate to data for ${fmtTitle(start)}.`);
  },
});

calendar.render();

function isoDate(d) {
  return d.toISOString().slice(0,10);
}

function updateUrlForWeek(start) {
  const q = new URLSearchParams(window.location.search);
  q.set('week', isoDate(start));
  const url = `${location.pathname}?${q.toString()}`;
  history.replaceState(null, '', url);
}

function setSelectedWeek(start) {
  // remove existing background event if present
  const existing = calendar.getEventById(bgEventId);
  if (existing) existing.remove();
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  calendar.addEvent({
    id: bgEventId,
    start: start.toISOString().slice(0,10),
    end: end.toISOString().slice(0,10),
    display: 'background',
    backgroundColor: 'rgba(15,108,93,0.12)',
    classNames: ['selected-week-bg']
  });
  updateWeekTitle(start);
  updateUrlForWeek(start);
}

// init selected week from URL if present
function initSelectedWeek() {
  const q = new URLSearchParams(window.location.search);
  const w = q.get('week');
  let start;
  if (w) {
    const parsed = new Date(w + 'T00:00:00Z');
    if (!isNaN(parsed)) start = startOfWeek(parsed);
  }
  if (!start) start = startOfWeek(earliestAvailable);
  if (start < earliestAvailable) {
    start = startOfWeek(earliestAvailable);
  }
  // move calendar to the week and highlight
  calendar.gotoDate(start);
  setSelectedWeek(start);
}

prev.addEventListener('click', () => {
  const current = calendar.getDate();
  const start = startOfWeek(current);
  const prevStart = new Date(start);
  prevStart.setDate(start.getDate() - 7);
  if (prevStart < earliestAvailable) {
    showToast('Data is only available from May 18, 2026.');
    return;
  }
  calendar.gotoDate(prevStart);
  setSelectedWeek(prevStart);
});

next.addEventListener('click', () => {
  const current = calendar.getDate();
  const start = startOfWeek(current);
  const nextStart = new Date(start);
  nextStart.setDate(start.getDate() + 7);
  calendar.gotoDate(nextStart);
  setSelectedWeek(nextStart);
});

// wire view button behavior already handled via dateClick/setSelectedWeek

// initialize from URL or earliest available
initSelectedWeek();
