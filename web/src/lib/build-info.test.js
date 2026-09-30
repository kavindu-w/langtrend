import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { describeBuild, formatBuildTime, getBuildInfo, getBuildInfoOnce } from './build-info.js';

const REPO = 'https://github.com/kavindu-w/langtrend';

describe('formatBuildTime', () => {
  it('formats in UTC with zero-padded time', () => {
    expect(formatBuildTime(new Date('2026-09-30T04:07:00Z'))).toBe('30 Sep 2026, 04:07 UTC');
  });
});

describe('describeBuild', () => {
  const builtAt = new Date('2026-09-30T12:00:00Z');

  it('builds labels and links when everything is known', () => {
    const info = describeBuild({ version: '0.3.1', sha: 'abcdef1234567890', builtAt, repoUrl: REPO });
    expect(info.versionLabel).toBe('v0.3.1');
    expect(info.versionUrl).toBe(`${REPO}/releases/tag/v0.3.1`);
    expect(info.shortSha).toBe('abcdef1');
    expect(info.commitUrl).toBe(`${REPO}/commit/abcdef1234567890`);
    expect(info.builtAtIso).toBe('2026-09-30T12:00:00.000Z');
  });

  it('degrades to nulls when version/sha are missing', () => {
    const info = describeBuild({ version: null, sha: null, builtAt, repoUrl: REPO });
    expect(info.versionLabel).toBeNull();
    expect(info.versionUrl).toBeNull();
    expect(info.shortSha).toBeNull();
    expect(info.commitUrl).toBeNull();
    expect(info.builtAtLabel).toBe('30 Sep 2026, 12:00 UTC');
  });
});

describe('getBuildInfo', () => {
  let tmp;
  afterEach(() => {
    if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
    tmp = undefined;
  });

  it('reads VERSION and falls back to GITHUB_SHA outside a git checkout', () => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'build-info-'));
    fs.writeFileSync(path.join(tmp, 'VERSION'), '1.2.3\n');
    const info = getBuildInfo({ repoRoot: tmp, env: { GITHUB_SHA: '1234567deadbeef' }, now: new Date('2026-01-01T00:00:00Z'), repoUrl: REPO });
    expect(info.versionLabel).toBe('v1.2.3');
    expect(info.shortSha).toBe('1234567');
  });

  it('returns a null version when VERSION is absent', () => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'build-info-'));
    const info = getBuildInfo({ repoRoot: tmp, env: {}, now: new Date(), repoUrl: REPO });
    expect(info.version).toBeNull();
    expect(info.shortSha).toBeNull();
  });
});

describe('getBuildInfoOnce', () => {
  it('returns the same snapshot on every call', () => {
    const first = getBuildInfoOnce({ repoUrl: REPO });
    const second = getBuildInfoOnce({ repoUrl: REPO, now: new Date('2000-01-01T00:00:00Z') });
    expect(second).toBe(first);
  });
});
