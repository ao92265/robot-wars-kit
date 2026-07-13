// Bundle the viewer + inline all 36 GLB models into one standalone HTML file.
// Usage: node build.mjs   ->  dist/robot-arena.html (open directly in a browser)

import { build } from 'esbuild';
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const glbDir = join(here, '..', 'robot-models', 'glb');
const outDir = join(here, 'dist');

const result = await build({
  entryPoints: [join(here, 'src', 'main.js')],
  bundle: true,
  minify: true,
  format: 'iife',
  write: false,
});
const bundle = result.outputFiles[0].text;

const glbs = {};
for (const f of readdirSync(glbDir).filter((f) => f.endsWith('.glb'))) {
  glbs[f.replace(/\.glb$/, '')] = readFileSync(join(glbDir, f)).toString('base64');
}
const names = Object.keys(glbs);
if (names.length !== 36) throw new Error(`expected 36 GLBs in ${glbDir}, found ${names.length}`);

// spinner weapon attachments (spinner_<shape>.glb -> shape)
const spinDir = join(here, '..', 'robot-models', 'glb-spinners');
const spinners = {};
try {
  for (const f of readdirSync(spinDir).filter((f) => f.endsWith('.glb'))) {
    spinners[f.slice(8, -4)] = readFileSync(join(spinDir, f)).toString('base64');
  }
} catch { /* no spinners built yet — viewer still works */ }

const html = readFileSync(join(here, 'src', 'template.html'), 'utf8')
  .replace('/*__GLB_DATA__*/',
    `window.ROBOT_GLB=${JSON.stringify(glbs)};window.SPINNER_GLB=${JSON.stringify(spinners)};`)
  .replace('/*__BUNDLE__*/', () => bundle);

mkdirSync(outDir, { recursive: true });
const out = join(outDir, 'robot-arena.html');
writeFileSync(out, html);
console.log(`wrote ${out} (${(html.length / 1024 / 1024).toFixed(2)} MB, ${names.length} robots)`);
