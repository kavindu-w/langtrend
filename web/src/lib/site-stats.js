// Client-side "usage" counters for the footer: site visits (GoatCounter's
// public counter endpoint) and GitHub repo stars/forks (public REST API, no
// token). Both are fetched in the browser at view time so the numbers stay
// live between weekly deploys; any failure just leaves that stat hidden.

export function compactNumber(n) {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return null;
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0).replace(/\.0$/, '')}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

// GoatCounter returns {"count": "1,234", "count_unique": ...}; older
// versions/self-hosted instances may send plain numbers or use a
// locale-specific thousands separator (",", ".", " ", or a thin space).
export function parseGoatCounterCount(payload) {
  const raw = payload?.count;
  if (typeof raw === 'number') return raw;
  if (typeof raw !== 'string') return null;
  const digits = raw.replace(/[^\d]/g, '');
  return digits ? Number(digits) : null;
}

export function parseRepoStats(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const { stargazers_count: stars, forks_count: forks } = payload;
  if (typeof stars !== 'number' || typeof forks !== 'number') return null;
  return { stars, forks };
}

export function goatCounterTotalUrl(code) {
  return code ? `https://${code}.goatcounter.com/counter/TOTAL.json` : null;
}

export function repoApiUrl(repoUrl) {
  const match = /github\.com\/([^/]+)\/([^/]+?)\/?$/.exec(repoUrl ?? '');
  return match ? `https://api.github.com/repos/${match[1]}/${match[2]}` : null;
}

// Build-time snapshot of the visit count, rendered into the page so the
// footer shows it even where the browser's live request is blocked (privacy
// extensions block *.goatcounter.com, while GitHub's API usually passes). The
// client still refreshes it live when it can. Never fails the build: any
// error or slow response just means no snapshot.
export async function fetchVisitCount(code, { fetchImpl = globalThis.fetch, timeoutMs = 5000 } = {}) {
  const url = goatCounterTotalUrl(code);
  if (!url || typeof fetchImpl !== 'function') return null;
  try {
    const res = await fetchImpl(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(timeoutMs) });
    return res.ok ? parseGoatCounterCount(await res.json()) : null;
  } catch {
    return null;
  }
}

// One request per build, shared by every page (a build renders ~30).
let visitCountPromise;
export function fetchVisitCountOnce(code, options) {
  visitCountPromise ??= fetchVisitCount(code, options);
  return visitCountPromise;
}
