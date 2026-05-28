import { defineConfig } from 'astro/config';
import vercel from '@astrojs/vercel';
import { readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

function walkDir(dir) {
  if (!existsSync(dir)) return [];
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkDir(full));
    } else {
      files.push(full);
    }
  }
  return files;
}

const dataDir = fileURLToPath(new URL('data', import.meta.url));
const dataFiles = walkDir(dataDir);

export default defineConfig({
  output: 'server',
  adapter: vercel({
    includeFiles: dataFiles,
  }),
  site: 'https://langtrend.vercel.app',
});
