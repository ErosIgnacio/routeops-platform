'use strict';

// This is a local HTTP boundary, not authentication. Internal SolverGateway
// calls and command-line clients have no Origin and remain supported.
const http = require('node:http');
const hosts = new Set(['vroom', 'localhost', '127.0.0.1', '[::1]']);
const origins = new Set(['http://localhost:5173', 'http://127.0.0.1:5173']);
const originalEmit = http.Server.prototype.emit;

function rejection(req) {
  let host;
  try {
    const raw = req.headers.host;
    if (!raw || /[\s/@?#\\]/.test(raw)) return [400, 'HOST_NOT_ALLOWED'];
    host = new URL(`http://${raw}`).hostname;
  } catch {
    return [400, 'HOST_NOT_ALLOWED'];
  }
  if (!hosts.has(host)) return [400, 'HOST_NOT_ALLOWED'];
  const origin = req.headers.origin;
  if ((origin !== undefined && !origins.has(origin)) ||
      req.headers['sec-fetch-site'] === 'cross-site') return [403, 'ORIGIN_NOT_ALLOWED'];
  if (req.url.length > 2048 || req.url.includes('?')) return [400, 'REQUEST_NOT_SUPPORTED'];
  if (req.method === 'POST') {
    const mediaType = (req.headers['content-type'] || '').split(';', 1)[0].trim().toLowerCase();
    if (mediaType !== 'application/json') return [415, 'JSON_REQUIRED'];
    const length = req.headers['content-length'];
    if (length !== undefined && (!/^\d{1,10}$/.test(length) || Number(length) > 1048576)) {
      return [413, 'BODY_LIMIT'];
    }
  } else if (req.method !== 'GET' && req.method !== 'HEAD') {
    return [405, 'METHOD_NOT_ALLOWED'];
  }
  return null;
}

http.Server.prototype.emit = function (event, ...args) {
  if (event === 'request') {
    const [req, res] = args;
    const failure = rejection(req);
    if (failure) {
      res.writeHead(failure[0], {'Content-Type': 'application/json', 'Connection': 'close'});
      res.end(JSON.stringify({code: failure[1]}));
      req.resume();
      return true;
    }
  }
  return originalEmit.call(this, event, ...args);
};
