import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

// Build-time metadata shown in the site footer: release version (repo-root
// VERSION file), the commit the build was made from, and when it was built.
// Evaluated once per `astro build`, so "built at" is effectively the deploy time.

function readVersion(repoRoot) {
  try {
    const raw = fs.readFileSync(path.join(repoRoot, 'VERSION'), 'utf-8').trim();
    return raw || null;
  } catch {
    return null;
  }
}

function readCommitSha(repoRoot, env) {
  // GITHUB_SHA is the trigger-time commit, not what deploy.yml checked out
  // (it checks out ref_name), so prefer asking git directly.
  try {
    return execSync('git rev-parse HEAD', { cwd: repoRoot, stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim() || null;
  } catch {
    return env.GITHUB_SHA || null;
  }
}

export function formatBuildTime(date) {
  // Fixed UTC formatting so the footer reads the same regardless of the
  // build machine's locale/timezone.
  const pad = (n) => String(n).padStart(2, '0');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${date.getUTCFullYear()}, ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

export function describeBuild({ version, sha, builtAt, repoUrl }) {
  const shortSha = sha ? sha.slice(0, 7) : null;
  return {
    version,
    versionLabel: version ? `v${version}` : null,
    versionUrl: version && repoUrl ? `${repoUrl}/releases/tag/v${version}` : null,
    shortSha,
    commitUrl: sha && repoUrl ? `${repoUrl}/commit/${sha}` : null,
    builtAtIso: builtAt.toISOString(),
    builtAtLabel: formatBuildTime(builtAt),
  };
}

export function getBuildInfo({ repoRoot = path.resolve(process.cwd(), '..'), env = process.env, now = new Date(), repoUrl } = {}) {
  return describeBuild({
    version: readVersion(repoRoot),
    sha: readCommitSha(repoRoot, env),
    builtAt: now,
    repoUrl,
  });
}

// One snapshot per build: BaseLayout renders on every page, and without this
// each page would run `git rev-parse` again and stamp its own "Last updated"
// time, seconds apart from its neighbours.
let cached;
export function getBuildInfoOnce(options) {
  cached ??= getBuildInfo(options);
  return cached;
}
