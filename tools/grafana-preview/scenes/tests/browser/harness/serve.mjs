// Serves the harness on loopback, sending other paths to index.html so that deep links load.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const root = process.env.OUT ?? '/tmp/scenes-harness';
const port = Number(process.env.PORT ?? 3418);
// Grafana's icons are not part of the harness, and an IconButton has no size without one, so every icon is the same neutral dot.
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="currentColor"/></svg>';

http.createServer((request, response) => {
  const { pathname } = new URL(request.url, 'http://local');
  if (pathname.endsWith('.svg')) {
    response.writeHead(200, { 'content-type': 'image/svg+xml' });
    response.end(ICON);
    return;
  }
  const file = pathname === '/main.js' ? 'main.js' : 'index.html';
  response.writeHead(200, { 'content-type': file.endsWith('js') ? 'text/javascript' : 'text/html' });
  fs.createReadStream(path.join(root, file)).pipe(response);
}).listen(port, '127.0.0.1', () => console.log('harness on', port));
