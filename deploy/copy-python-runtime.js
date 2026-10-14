import { copyFile, mkdir, writeFile } from 'node:fs/promises';
await mkdir('dist/python-runtime', { recursive: true });
for (const name of ['pyodide.js', 'pyodide.asm.js', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json']) {
  await copyFile(`node_modules/pyodide/${name}`, `dist/python-runtime/${name}`);
}
await writeFile('dist/python-runtime/NOTICE.txt', 'Pyodide 0.27.7 — Mozilla Public License 2.0\nhttps://github.com/pyodide/pyodide/tree/0.27.7\nIncludes CPython and its standard library, under the Python Software Foundation license.\nRuntime artifacts are copied unchanged from the integrity-checked npm package.\n');
for (const name of ['MPL-2.0.txt', 'PYTHON-LICENSE.txt']) {
  await copyFile(`deploy/python-licenses/${name}`, `dist/python-runtime/${name}`);
}
