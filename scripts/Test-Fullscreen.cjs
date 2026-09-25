const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, firefox, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

// Run with Playwright installed: node scripts/Test-Fullscreen.cjs
const root = path.resolve(__dirname, '../site');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.png': 'image/png', '.mp4': 'video/mp4' };
const server = http.createServer((req, res) => {
  const name = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(root, '.' + (name === '/' ? '/index.html' : name));
  if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
  fs.readFile(file, (error, data) => {
    if (error) { res.writeHead(404).end(); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
});

async function checkImage(page, selector, native) {
  const trigger = page.locator(selector).first();
  await trigger.scrollIntoViewIfNeeded();
  await page.waitForFunction(el => { const img = el.parentElement.querySelector("img"); return img.complete && img.naturalWidth > 0 && img.currentSrc; }, await trigger.elementHandle());
  const expected = await trigger.evaluate(el => { const img = el.parentElement.querySelector("img"); return { src: img.currentSrc || img.src, alt: img.alt }; });
  if (await page.evaluate(() => navigator.maxTouchPoints > 0)) await trigger.tap();
  else await trigger.click();
  await page.waitForFunction(() => document.querySelector('.image-fullscreen-dialog').open);
  if (native) await page.waitForFunction(() => document.fullscreenElement?.classList.contains('image-fullscreen-view'));
  await page.waitForFunction(() => { const img = document.querySelector('.image-fullscreen-view img'); return img.complete && img.naturalWidth > 0; });
  assert.deepEqual(await page.locator(".image-fullscreen-view img").evaluate(img => ({ src: img.src, alt: img.alt })), expected);
  const originalViewport = page.viewportSize();
  if (!native && originalViewport.width === 390) await page.setViewportSize({ width: 844, height: 390 });
  await page.waitForFunction(() => { const rect = document.querySelector(".image-fullscreen-view").getBoundingClientRect(); return Math.abs(rect.width - innerWidth) <= 1 && Math.abs(rect.height - innerHeight) <= 1; });
  const state = await page.evaluate(() => {
    const view = document.querySelector('.image-fullscreen-view');
    const img = view.querySelector('img');
    const button = view.querySelector('button');
    const rect = view.getBoundingClientRect();
    const imageRect = img.getBoundingClientRect();
    const exit = button.getBoundingClientRect();
    return {
      fills: Math.abs(rect.width - innerWidth) <= 1 && Math.abs(rect.height - innerHeight) <= 1,
      fits: imageRect.width <= innerWidth + 1 && imageRect.height <= innerHeight + 1,
      contain: getComputedStyle(img).objectFit === 'contain',
      exitVisible: exit.right <= innerWidth && exit.bottom <= innerHeight && exit.width >= 44 && exit.height >= 44,
      focus: document.activeElement === button,
      locked: getComputedStyle(document.documentElement).overflow === 'hidden',
      alt: img.alt,
    };
  });
  if (!Object.entries(state).every(([, value]) => Boolean(value))) console.log(await page.evaluate(() => ({ width: innerWidth, height: innerHeight, image: document.querySelector(".image-fullscreen-view img").getBoundingClientRect().toJSON(), view: document.querySelector(".image-fullscreen-view").getBoundingClientRect().toJSON() })));
  assert.ok(Object.entries(state).every(([, value]) => Boolean(value)), JSON.stringify(state));
  if (process.env.FULLSCREEN_SCREENSHOTS && native && page.context().browser().browserType().name() === "firefox") await page.waitForTimeout(500);
  if (process.env.FULLSCREEN_SCREENSHOTS) await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  if (process.env.FULLSCREEN_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.FULLSCREEN_SCREENSHOTS, `${page.context().browser().browserType().name()}-${page.viewportSize().width}-${native ? "native" : "fallback"}.png`) });
  if (!native && originalViewport.width === 390) await page.setViewportSize(originalViewport);
  await page.locator('.image-fullscreen-view button').click();
  await page.waitForFunction(() => !document.querySelector('.image-fullscreen-dialog').open && !document.fullscreenElement && !document.documentElement.classList.contains('image-fullscreen-open'));
  assert.equal(await trigger.evaluate(el => document.activeElement === el && el.getAttribute('aria-pressed') === 'false'), true);
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains('image-fullscreen-open')), false);
}

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  for (const [name, engine] of Object.entries({ chromium, firefox })) {
    const browser = await engine.launch({ headless: true, ...(name === "firefox" ? { env: { ...process.env, MOZ_HEADLESS_WIDTH: "1440", MOZ_HEADLESS_HEIGHT: "900" }, firefoxUserPrefs: { "full-screen-api.transition-duration.enter": "0 0", "full-screen-api.transition-duration.leave": "0 0" } } : {}) });
    try {
      for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
        const page = await browser.newPage({ viewport, reducedMotion: 'reduce', hasTouch: Math.min(viewport.width, viewport.height) < 500 });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(url);
        for (const selector of ['.work-card-media--duo picture > button', '.work-card-media:not(.work-card-media--duo):has(picture) > button', '.pen-item picture > button', '.woodwork-item picture > button']) {
          await checkImage(page, selector, true);
        }
        // Browser-driven exit must also close the viewer and unlock the page.
        await page.locator('.pen-item picture > button').first().click();
        await page.waitForFunction(() => !!document.fullscreenElement);
        // Let fullscreenchange run before simulating the browser's exit action.
        await page.waitForFunction(() => document.querySelector('.pen-item picture > button').textContent === 'Exit fullscreen');
        await page.evaluate(() => document.exitFullscreen());
        await page.waitForFunction(() => !document.querySelector('.image-fullscreen-dialog').open && !document.documentElement.classList.contains('image-fullscreen-open'));
        // Existing video controls still use native fullscreen and contain sizing.
        await page.locator('.work-card-media:has(video) > button').first().click();
        await page.waitForFunction(() => !!document.fullscreenElement);
        assert.equal(await page.evaluate(() => getComputedStyle(document.fullscreenElement.querySelector('video')).objectFit), 'contain');
        await page.locator('.work-card-media:has(video) > button').first().click();
        await page.waitForFunction(() => !document.fullscreenElement);
        for (const mode of ['missing', 'rejected']) {
          await page.evaluate(mode => {
            Element.prototype.requestFullscreen = mode === 'missing' ? undefined : () => Promise.reject(new Error('Denied'));
            Element.prototype.webkitRequestFullscreen = undefined;
          }, mode);
          await checkImage(page, '.pen-item picture > button', false);
          await page.locator('.woodwork-item picture > button').first().click();
          await page.keyboard.press('Escape');
          await page.waitForFunction(() => !document.querySelector('.image-fullscreen-dialog').open && !document.documentElement.classList.contains('image-fullscreen-open'));
        }
        assert.deepEqual(errors, []);
        console.log(`PASS ${name} ${viewport.width}x${viewport.height}: images, video, native exit, missing/denied API, Escape, focus, sizing`);
        await page.close();
      }
    } finally { await browser.close(); }
  }
  const browser = await webkit.launch({ headless: true });
  try {
    for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) {
      const page = await browser.newPage({ ...devices['iPhone 13'], viewport, reducedMotion: 'reduce' });
      await page.addInitScript(() => {
        // iPhone browsers may only expose native fullscreen for video.
        Element.prototype.requestFullscreen = undefined;
        Element.prototype.webkitRequestFullscreen = undefined;
      });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(url);
      for (const selector of ['.work-card-media--duo picture > button', '.pen-item picture > button', '.woodwork-item picture > button']) {
        await checkImage(page, selector, false);
      }
      assert.deepEqual(errors, []);
      console.log(`PASS webkit iPhone ${viewport.width}x${viewport.height}: touch, fallback, rotation, sizing, exit, focus`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
