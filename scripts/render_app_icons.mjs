// Build tooling only: render the canonical SVG paths, without a browser or CDN.
import { writeFile } from 'node:fs/promises';
import { Resvg } from '@resvg/resvg-js';

let input = '';
for await (const chunk of process.stdin) input += chunk;
for (const { svg, size, path } of JSON.parse(input)) {
  const image = new Resvg(svg, {
    fitTo: { mode: 'width', value: size },
    font: { loadSystemFonts: false },
  });
  await writeFile(path, image.render().asPng());
}
