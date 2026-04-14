import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      // SPA mode: all routes fall back to index.html
      fallback: 'index.html'
    }),
    alias: {
      $components: 'src/components',
      $lib: 'src/lib'
    }
  }
};

export default config;
