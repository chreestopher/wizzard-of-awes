const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, firefox, webkit } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  for (const engine of [chromium, firefox, webkit]) {
    const browser = await engine.launch();
    try {
      const page = await browser.newPage();
      let creates = 0, uploads = 0, submits = 0;
      await page.route('https://example.com/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === '/api/inquiries') {
          creates++;
          return route.fulfill({ json: { inquiryId: 'test', token: 'test-token', uploads: [{
            url: 'https://example.com/upload', fields: { policy: 'signed-policy', key: 'test-key', 'Content-Type': 'image/png' }
          }] } });
        }
        if (pathname === '/upload') {
          uploads++;
          assert.equal(route.request().method(), 'POST');
          const body = route.request().postDataBuffer().toString();
          assert.match(route.request().headers()['content-type'], /^multipart\/form-data; boundary=/);
          assert.ok(body.indexOf('signed-policy') < body.indexOf('filename="art.png"'));
          assert.ok(body.includes('test-image-data'));
          return route.fulfill({ status: 204 });
        }
        if (pathname === '/api/inquiries/test/submit') {
          submits++;
          assert.deepEqual(route.request().postDataJSON(), { token: 'test-token' });
          return route.fulfill({ status: submits === 1 ? 503 : 202, json: {
            message: submits === 1 ? 'Email rejected; retry.' : 'Your request is saved; email delivery is pending.'
          } });
        }
        if (pathname === '/' || pathname === '/app.js' || pathname === '/styles.css') {
          return route.fulfill({ body: fs.readFileSync(path.join(__dirname, '../site', pathname === '/' ? 'index.html' : pathname.slice(1))),
            contentType: pathname === '/' ? 'text/html' : pathname.endsWith('.js') ? 'application/javascript' : 'text/css' });
        }
        return route.fulfill({ status: 404 });
      });
      await page.goto('https://example.com/');
      await page.locator('[name=name]').fill('Test');
      await page.locator('[name=email]').fill('visitor@example.com');
      await page.locator('[name=projectType]').selectOption({ index: 1 });
      await page.locator('[name=message]').fill('Test inquiry');
      await page.locator('#file-input').setInputFiles({ name: 'art.png', mimeType: 'image/png', buffer: Buffer.from('test-image-data') });
      await page.locator('button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('#form-status').textContent === 'Email rejected; retry.');
      await page.locator('button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('#form-status').textContent === 'Your request is saved; email delivery is pending.');
      assert.deepEqual({ creates, uploads, submits }, { creates: 1, uploads: 1, submits: 2 });
      console.log(`PASS ${engine.name()}: multipart upload, same-token retry, truthful pending message`);
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
