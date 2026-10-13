import { defineConfig } from 'vite';
export default defineConfig({
  base: '/cloud/',
  preview: { proxy: { '/cloud/api/': {
    target: 'http://127.0.0.1:18992',
    headers: { 'X-Cloud-Token': 'test-only-token' },
    rewrite: path => path.replace(/^\/cloud\/api/, ''),
  } } },
});
