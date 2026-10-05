'use strict';
const {test, after} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
require('./request-guard.cjs');
let reached = 0;
const server = http.createServer((req, res) => { reached++; req.resume(); res.end('ok'); });
const listening = new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
after(() => new Promise(resolve => server.close(resolve)));
async function request(headers = {}, method = 'POST', path = '/') {
  await listening;
  return new Promise((resolve, reject) => {
    const req = http.request({hostname: '127.0.0.1', port: server.address().port, method, path,
      headers: {'Content-Type': 'application/json', ...headers}}, res => {
      let body = ''; res.on('data', chunk => body += chunk);
      res.on('end', () => resolve({status: res.statusCode, body}));
    });
    req.on('error', reject); req.end();
  });
}
test('trusted internal, local browser and health requests reach the server', async () => {
  for (const headers of [{Host:'vroom:3000'}, {}, {Origin:'http://localhost:5173'},
    {Origin:'http://127.0.0.1:5173'}]) assert.equal((await request(headers)).status, 200);
  assert.equal((await request({}, 'GET', '/health')).status, 200);
});
test('foreign/null origins and DNS rebinding rejected before handler', async () => {
  const before = reached;
  for (const headers of [{Origin:'https://untrusted.example'}, {Origin:'null'},
    {'Sec-Fetch-Site':'cross-site'}, {Host:'untrusted.example'}, {Host:'vroom@untrusted.example'},
    {Host:'vroom:3000?ignored'}]) assert.ok((await request(headers)).status >= 400);
  assert.equal(reached, before);
});
test('form/query parsing, unsupported methods and declared over-limit body rejected', async () => {
  const before = reached;
  assert.equal((await request({'Content-Type':'application/x-www-form-urlencoded'})).status, 415);
  assert.equal((await request({}, 'GET', '/?a[b]=1')).status, 400);
  assert.equal((await request({}, 'POST', '/'+'x'.repeat(2048))).status, 400);
  assert.equal((await request({}, 'OPTIONS')).status, 405);
  assert.equal((await request({'Content-Length':'1048577'})).status, 413);
  assert.equal(reached, before);
});
