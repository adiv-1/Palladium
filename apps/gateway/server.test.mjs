import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {once} from 'node:events';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createGateway} from './server.mjs';
import {gzipSync} from 'node:zlib';

async function listen(server) {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  return `http://127.0.0.1:${server.address().port}`;
}
async function fixture(t, handler) {
  const dist = await mkdtemp(join(tmpdir(), 'safesc-gateway-'));
  await writeFile(join(dist, 'index.html'), '<h1>SafeSC</h1>');
  const backend = http.createServer(handler || ((_req, res) => res.end('upstream')));
  const backendOrigin = await listen(backend);
  const gateway = createGateway({backendOrigin, dist});
  const origin = await listen(gateway);
  t.after(async () => {
    gateway.closeStreams(); gateway.closeAllConnections(); backend.closeAllConnections();
    await Promise.all([new Promise(r => gateway.close(r)), new Promise(r => backend.close(r))]);
    await rm(dist, {recursive:true});
  });
  return {gateway, backend, origin, dist};
}

test('bundled demo videos support media types, seeking, suffixes and HEAD requests', async t => {
  const {origin, dist} = await fixture(t);
  await writeFile(join(dist, 'demo.mp4'), '0123456789');
  const full = await fetch(origin + '/demo.mp4');
  assert.equal(full.headers.get('content-type'), 'video/mp4');
  assert.equal(full.headers.get('accept-ranges'), 'bytes');
  assert.equal(await full.text(), '0123456789');
  for (const [range, expected, contentRange] of [
    ['bytes=3-5', '345', 'bytes 3-5/10'],
    ['bytes=7-', '789', 'bytes 7-9/10'],
    ['bytes=-2', '89', 'bytes 8-9/10'],
    ['bytes=8-100', '89', 'bytes 8-9/10'],
  ]) {
    const response = await fetch(origin + '/demo.mp4', {headers:{Range:range}});
    assert.equal(response.status, 206);
    assert.equal(response.headers.get('content-range'), contentRange);
    assert.equal(response.headers.get('content-length'), String(expected.length));
    assert.equal(await response.text(), expected);
  }
  const head = await fetch(origin + '/demo.mp4', {method:'HEAD', headers:{Range:'bytes=3-5'}});
  assert.equal(head.status, 200);
  assert.equal(head.headers.get('content-length'), '10');
  assert.equal(await head.text(), '');
});

test('invalid or unsatisfiable bundled-video ranges return 416 without file contents', async t => {
  const {origin, dist} = await fixture(t);
  await writeFile(join(dist, 'demo.mp4'), '0123456789');
  for (const range of ['bytes=10-', 'bytes=7-3', 'bytes=-0', 'bytes=-', 'bytes=0-1,4-5']) {
    const response = await fetch(origin + '/demo.mp4', {headers:{Range:range}});
    assert.equal(response.status, 416, range);
    assert.equal(response.headers.get('content-range'), 'bytes */10');
    assert.equal(await response.text(), '');
  }
});

test('serves phone SPA and readiness without backend, excludes private files', async t => {
  const {origin} = await fixture(t);
  assert.match(await (await fetch(origin + '/capture')).text(), /SafeSC/);
  assert.equal((await fetch(origin + '/')).headers.get('referrer-policy'), 'strict-origin-when-cross-origin');
  assert.equal((await fetch(origin + '/_health')).status, 200);
  for (const path of ['/.env', '/data/private.json', '/%2eenv', '/missing.js']) {
    assert.equal((await fetch(origin + path)).status, 404, path);
  }
  assert.equal((await fetch(origin + '/', {method:'POST'})).status, 405);
});

test('streams upload body, auth cookies, compressed responses and media ranges', async t => {
  const {origin} = await fixture(t, async (req, res) => {
    if (req.url === '/api/login') {
      let body = ''; for await (const chunk of req) body += chunk;
      assert.equal(body, '{"token":"test-only"}');
      assert.equal(req.headers['x-forwarded-host'], undefined);
      res.setHeader('Set-Cookie', 'safesc_token=test-only; HttpOnly; Secure; SameSite=Strict; Path=/');
      res.setHeader('Content-Encoding', 'gzip');
      return res.end(gzipSync('{"ok":true}'));
    }
    assert.equal(req.headers.cookie, 'safesc_token=test-only');
    assert.equal(req.headers.range, 'bytes=3-5');
    res.writeHead(206, {'Content-Range':'bytes 3-5/10', 'Content-Type':'video/mp4'});
    res.end('345');
  });
  const login = await fetch(origin + '/api/login', {method:'POST', body:'{"token":"test-only"}',
    headers:{'x-forwarded-host':'attacker.invalid'}});
  assert.match(login.headers.get('set-cookie'), /HttpOnly; Secure; SameSite=Strict/);
  assert.deepEqual(await login.json(), {ok:true});
  const media = await fetch(origin + '/api/video', {headers:{cookie:'safesc_token=test-only', range:'bytes=3-5'}});
  assert.equal(media.status, 206);
  assert.equal(media.headers.get('content-range'), 'bytes 3-5/10');
  assert.equal(media.headers.get('cache-control'), 'private, no-store');
  assert.equal(await media.text(), '345');
});

test('passes backend auth failures and reports unreachable workers', async t => {
  const {origin, backend} = await fixture(t, (_req, res) => { res.writeHead(401); res.end('Sign in'); });
  assert.equal((await fetch(origin + '/api/health')).status, 401);
  backend.closeAllConnections(); await new Promise(r => backend.close(r));
  assert.equal((await fetch(origin + '/api/health')).status, 502);
});

test('relays WebSocket cookies and binary data, rejects unrelated paths and origins', async t => {
  const {origin, backend} = await fixture(t);
  backend.on('upgrade', (req, socket, head) => {
    assert.equal(req.headers.cookie, 'safesc_token=test-only');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
    if (head.length) socket.write(head);
    socket.pipe(socket);
  });
  const socket = await new Promise((resolve, reject) => {
    const request = http.request(origin + '/ws/live', {headers:{Connection:'Upgrade', Upgrade:'websocket',
      Origin:origin, Cookie:'safesc_token=test-only'}});
    request.on('upgrade', (_response, socket) => resolve(socket)); request.on('error', reject); request.end();
  });
  const binary = Buffer.from([0x82, 0x83, 1, 2, 3, 4, 42, 43, 44]);
  const data = once(socket, 'data'); socket.write(binary);
  assert.deepEqual((await data)[0], binary); socket.destroy();
  for (const [path, from, expected] of [['/ws/capture', 'https://evil.invalid', 403], ['/ws/admin', origin, 404]]) {
    const status = await new Promise((resolve, reject) => {
      const request = http.request(origin + path, {headers:{Connection:'Upgrade', Upgrade:'websocket', Origin:from}});
      request.on('response', response => { response.resume(); resolve(response.statusCode); });
      request.on('error', reject); request.end();
    });
    assert.equal(status, expected);
  }
});

test('requires a fixed HTTPS worker origin and forbids credentials or URL paths', () => {
  for (const backendOrigin of ['http://example.com', 'https://user:password@example.com', 'https://example.com/api', 'https://example.com/?token=x']) {
    assert.throws(() => createGateway({backendOrigin}));
  }
});
