import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  output: 'static',
  site: 'https://kavindu-w.github.io',
  base: '/langtrend/',
  // Emits /langtrend/sitemap-index.xml for Google Search Console.
  integrations: [sitemap()],
});
