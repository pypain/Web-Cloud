import { defineConfig } from 'vite';
import { createReadStream } from 'node:fs';
import { resolve } from 'node:path';

const files = new Set(['pyodide.js', 'pyodide.asm.js', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json']);

export default defineConfig({ plugins: [{
  name: 'local-python-runtime',
  configureServer(server) {
    const prefix = server.config.base + 'python-runtime/';
    server.middlewares.use((req, res, next) => {
      const path = new URL(req.url, 'http://localhost').pathname;
      if (!path.startsWith(prefix)) return next();
      const name = path.slice(prefix.length);
      if (!files.has(name)) { res.statusCode = 404; res.end(); return; }
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
      res.setHeader('Content-Type', name.endsWith('.js') ? 'application/javascript' : name.endsWith('.wasm') ? 'application/wasm' : name.endsWith('.json') ? 'application/json' : 'application/octet-stream');
      const stream = createReadStream(resolve('node_modules/pyodide', name));
      stream.on('error', () => { res.statusCode = 404; res.end(); });
      stream.pipe(res);
    });
  },
}] });
