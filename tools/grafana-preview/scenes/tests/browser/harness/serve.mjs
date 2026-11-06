// Serves the harness on loopback, sending other paths to index.html so that deep links load.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const root = process.env.OUT ?? '/tmp/scenes-harness';
const port = Number(process.env.PORT ?? 3418);
// Grafana's icons are not part of the harness, and an IconButton has no size without one, so every icon is the same neutral dot.
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="currentColor"/></svg>';

export function createHarnessServer(root) {
  return http.createServer((request, response) => {
    const { pathname } = new URL(request.url, 'http://local');
    if (pathname.endsWith('.svg')) {
      response.writeHead(200, { 'content-type': 'image/svg+xml' });
      response.end(ICON);
      return;
    }
    const file = pathname === '/main.js' ? 'main.js' : 'index.html';
    // Open before sending success headers, including the race where a build output disappears.
    const stream = fs.createReadStream(path.join(root, file));
    stream.on('error', (error) => {
      if (response.headersSent) return response.destroy(error);
      response.writeHead(error.code === 'ENOENT' ? 404 : 500, { 'content-type': 'text/plain' });
      response.end('Harness output unavailable. Build the harness before serving it.');
    });
    stream.on('open', () => {
      response.writeHead(200, { 'content-type': file.endsWith('js') ? 'text/javascript' : 'text/html' });
      stream.pipe(response);
    });
  });
}
if (process.argv[1] && path.resolve(process.argv[1]) === import.meta.filename) {
  createHarnessServer(root).listen(port, '127.0.0.1', () => console.log('harness on', port));
}
