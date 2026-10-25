import { cpSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const destination = resolve('src/photoframe/static/fonts');
mkdirSync(destination, { recursive: true });

for (const [family, subsets] of [
  ['inter', ['cyrillic-ext', 'cyrillic', 'greek-ext', 'greek', 'vietnamese', 'latin-ext', 'latin']],
  ['newsreader', ['vietnamese', 'latin-ext', 'latin']],
]) {
  const packageRoot = resolve(`node_modules/@fontsource-variable/${family}`);
  for (const subset of subsets) {
    const filename = `${family}-${subset}-wght-normal.woff2`;
    cpSync(resolve(packageRoot, 'files', filename), resolve(destination, filename));
  }
  cpSync(resolve(packageRoot, 'LICENSE'), resolve(destination, `${family}-OFL.txt`));
}
