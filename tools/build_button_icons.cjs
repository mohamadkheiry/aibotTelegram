/* Reproducible export of the licensed Lucide SVG sources to Telegram emoji.
 * Tool-only dependencies: Node.js, sharp, project Python; no bot network call.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const sharp = require(process.env.SHARP_MODULE || 'sharp');

async function main() {
  const input = process.argv[process.argv.indexOf('--lucide-dir') + 1];
  if (!process.argv.includes('--lucide-dir') || !input) throw new Error('--lucide-dir must point to lucide-static 1.41.0');
  const root = path.resolve(__dirname, '..');
  const source = path.resolve(input);
  const metadata = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8'));
  if (metadata.name !== 'lucide-static' || metadata.version !== '1.41.0') throw new Error('Unexpected Lucide package version');
  const icons = JSON.parse(execFileSync(process.env.PYTHON_PATH || 'python', ['-B', '-c', 'import json; from app.button_icons import ICON_SOURCES; print(json.dumps(ICON_SOURCES))'], { cwd: root, encoding: 'utf8' }));
  const output = path.join(root, 'assets', 'button-icons');
  fs.mkdirSync(path.join(output, 'svg'), { recursive: true });
  fs.mkdirSync(path.join(output, 'webp'), { recursive: true });
  fs.copyFileSync(path.join(source, 'LICENSE'), path.join(output, 'LICENSE-Lucide.txt'));
  const manifest = { package: 'lucide-static', version: metadata.version, source: 'https://registry.npmjs.org/lucide-static/-/lucide-static-1.41.0.tgz',
    integrity: 'sha512-39fX7SH+Rwis0oUmLLOipOoFSiJll9yi2DyEGDaE7Sp0qQAEhEfMQ2scQNdWKeGVENGv1uXc5ZeZqBWsuhQSFg==',
    needs_repainting: true, icons: {} };
  for (const [key, name] of Object.entries(icons)) {
    const svg = fs.readFileSync(path.join(source, 'icons', name + '.svg'), 'utf8');
    if (/<script|<image|<foreignObject|href=/i.test(svg)) throw new Error('Unexpected external or active SVG content');
    fs.copyFileSync(path.join(source, 'icons', name + '.svg'), path.join(output, 'svg', key + '.svg'));
    const white = svg.replace(/currentColor/g, '#ffffff').replace(/width="24"/, 'width="100"').replace(/height="24"/, 'height="100"');
    const file = path.join(output, 'webp', key + '.webp');
    await sharp(Buffer.from(white)).resize(100, 100).webp({ lossless: true }).toFile(file);
    const image = await sharp(file).metadata();
    if (image.width !== 100 || image.height !== 100 || !image.hasAlpha || fs.statSync(file).size > 128 * 1024) throw new Error('Invalid Telegram emoji image');
    manifest.icons[key] = { lucide: name, svg: 'svg/' + key + '.svg', webp: 'webp/' + key + '.webp',
      sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') };
  }
  fs.writeFileSync(path.join(output, 'sources.json'), JSON.stringify(manifest, null, 2) + '\n');
  if (process.argv.includes('--preview-out')) {
    const preview = process.argv[process.argv.indexOf('--preview-out') + 1];
    if (!preview) throw new Error('--preview-out needs a file path');
    const cells = Object.entries(icons).map(([key], index) => {
      const x = 24 + (index % 6) * 156, y = 76 + Math.floor(index / 6) * 110;
      const svg = fs.readFileSync(path.join(output, 'svg', key + '.svg'), 'utf8');
      const shape = svg.slice(svg.indexOf('>', svg.indexOf('<svg')) + 1, svg.lastIndexOf('</svg>'));
      return `<g transform="translate(${x} ${y})"><rect width="140" height="88" rx="14" fill="#e2e8f0"/><rect x="70" width="70" height="88" rx="14" fill="#172033"/>
        <g transform="translate(17 19) scale(1.4)" fill="none" stroke="#172033" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${shape}</g>
        <g transform="translate(88 19) scale(1.4)" fill="none" stroke="#f8fafc" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${shape}</g>
        <text x="70" y="104" text-anchor="middle" font-family="Arial" font-size="12" fill="#475569">${key}</text></g>`;
    }).join('');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="976" height="980"><rect width="976" height="980" fill="#f8fafc"/>
      <text x="24" y="33" font-family="Arial" font-size="23" fill="#0f172a">Eleven Accounts / Lucide Minimal</text>
      <text x="24" y="56" font-family="Arial" font-size="13" fill="#64748b">45 adaptive icons / light + dark preview / not a Telegram screenshot</text>${cells}</svg>`;
    await sharp(Buffer.from(svg)).png().toFile(path.resolve(preview));
  }
  console.log(JSON.stringify({ icons: Object.keys(icons).length, format: 'lossless WebP RGBA 100x100', output }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
