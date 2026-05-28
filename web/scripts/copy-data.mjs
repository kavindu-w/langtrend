import { cpSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const repoRoot = join(webRoot, '..');
const src = join(repoRoot, 'data', 'processed');
const dst = join(webRoot, 'data', 'processed');

if (!existsSync(src)) {
  console.log('data/processed not found, skipping copy');
  process.exit(0);
}

const ROOT_FILES = [
  'langtrend_manifest_last_7_days.json',
  'language_data.json',
  'language_screening_warnings.json',
];
const EXCLUDED_DIRS = ['html_cache', 'pdf_cache', 'topic_clustering'];

for (const file of ROOT_FILES) {
  const fileSrc = join(src, file);
  if (existsSync(fileSrc)) cpSync(fileSrc, join(dst, file));
}

cpSync(join(src, 'weeks'), join(dst, 'weeks'), {
  recursive: true,
  filter: (srcPath) => {
    const name = srcPath.split('/').pop();
    return !EXCLUDED_DIRS.includes(name);
  },
});

console.log('Copied data/processed → web/data/processed');
