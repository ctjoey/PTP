// Renders the App Store icon (1024x1024 PNG) from the same artwork as static/img/icon.svg, but with
// a full-bleed square background: iOS applies its own corner mask, and Apple rejects icons with
// transparency or baked-in rounded corners. Matches static/img/apple-touch-icon.png.
//
// Usage (Playwright + its Chromium are needed; nothing is installed by this script):
//   NODE_PATH=$(npm root -g) node ios/scripts/render_icon.js [out.png] [size]
// With an opaque page background Chromium writes an 8-bit RGB PNG (no alpha channel), which is what
// App Store Connect requires. Check with:  file out.png   ->  "1024 x 1024, 8-bit/color RGB"
// At size 180 the output is pixel-identical to static/img/apple-touch-icon.png.
const path = require('path');
const { chromium } = require('playwright');

const out = path.resolve(process.argv[2] || path.join(__dirname, '..', 'PickThePlay', 'Assets.xcassets', 'AppIcon.appiconset', 'icon-1024.png'));
const size = parseInt(process.argv[3] || '1024', 10);

// Same geometry and colours as static/img/icon.svg; only the background rect loses its rx="28".
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="${size}" height="${size}">
  <rect width="128" height="128" fill="#070b12"/>
  <g transform="rotate(-35 64 64)">
    <ellipse cx="64" cy="64" rx="44" ry="26" fill="#2fd98b"/>
    <path d="M40 64h48" stroke="#03140b" stroke-width="5" stroke-linecap="round"/>
    <path d="M52 56v16M60 56v16M68 56v16M76 56v16" stroke="#03140b" stroke-width="4" stroke-linecap="round"/>
  </g>
</svg>`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(`<!doctype html><html><head><style>html,body{margin:0;padding:0;background:#070b12}svg{display:block}</style></head><body>${svg}</body></html>`);
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width: size, height: size }, omitBackground: false });
  await browser.close();
  console.log(`rendered ${out} (${size}x${size})`);
})().catch((e) => { console.error(e); process.exit(1); });
