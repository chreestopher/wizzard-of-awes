const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium, firefox, webkit } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  for (const engine of [chromium, firefox, webkit]) {
    const browser = await engine.launch();
    let server;
    try {
      const page = await browser.newPage();
      let creates = 0, uploads = 0, submits = 0;
      let origin;
      const handle = async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === '/api/inquiries') {
          creates++;
          return route.fulfill({ json: { inquiryId: 'test', token: 'test-token', uploads: [{
            url: origin + '/upload', fields: { policy: 'signed-policy', key: 'test-key', 'Content-Type': 'image/png' }
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
      };
      server = http.createServer(async (request, response) => {
        try {
          const chunks = [];
          for await (const chunk of request) chunks.push(chunk);
          const body = Buffer.concat(chunks);
          await handle({
            request: () => ({ url: () => origin + request.url, method: () => request.method,
              headers: () => request.headers, postDataBuffer: () => body, postDataJSON: () => JSON.parse(body) }),
            fulfill: async ({ status = 200, json, body: payload, contentType }) => {
              response.writeHead(status, { 'Content-Type': json ? 'application/json' : contentType || 'text/plain' });
              response.end(json ? JSON.stringify(json) : payload);
            }
          });
        } catch (error) { response.writeHead(500).end(); console.error(error); process.exitCode = 1; }
      });
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      origin = `http://127.0.0.1:${server.address().port}`;
      await page.goto(origin + '/');
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
    } finally { await browser.close(); if (server) server.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
