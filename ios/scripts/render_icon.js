// Renders the app icon from ios/scripts/app-icon.svg (the single source of truth: the "Play Call"
// mark, a chalk-talk X and O with one mint route arrow). Output is a full-bleed square with an opaque
// background: iOS applies its own corner mask, and Apple rejects icons with transparency or baked-in
// rounded corners.
//
// Usage (Playwright + its Chromium are needed; nothing is installed by this script):
//   NODE_PATH=$(npm root -g) node ios/scripts/render_icon.js [out.png] [size]
// Defaults to the App Store icon. Also used for the web icons:
//   ... render_icon.js static/img/apple-touch-icon.png 180   (and icon-192.png 192, icon-512.png 512)
// With an opaque page background Chromium writes an 8-bit RGB PNG (no alpha channel), which is what
// App Store Connect requires. Check with:  file out.png   ->  "1024 x 1024, 8-bit/color RGB"
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const out = path.resolve(process.argv[2] || path.join(__dirname, '..', 'PickThePlay', 'Assets.xcassets', 'AppIcon.appiconset', 'icon-1024.png'));
const size = parseInt(process.argv[3] || '1024', 10);
const svg = fs.readFileSync(path.join(__dirname, 'app-icon.svg'), 'utf8')
  .replace(/width="1024" height="1024"/, `width="${size}" height="${size}"`);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(`<!doctype html><html><head><style>html,body{margin:0;padding:0;background:#070b12}svg{display:block}</style></head><body>${svg}</body></html>`);
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width: size, height: size }, omitBackground: false });
  await browser.close();
  console.log(`wrote ${out} (${size}x${size})`);
})();
