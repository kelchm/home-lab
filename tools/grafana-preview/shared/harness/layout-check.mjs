// What jsdom can't see, measured in Chromium: nothing overlaps, no name is cut, and everything out of sight has a visible cue. Needs shoot.mjs to have built harness/out.
import assert from 'node:assert/strict';
import { chromium, launchOptions } from '../../checks/playwright.mjs';
import path from 'node:path';

const here = path.dirname(new URL(import.meta.url).pathname);
const browser = await chromium.launch(launchOptions);

const measure = () => {
  const out = { problems: [], fits: [] };
  const say = (text) => out.problems.push(text);
  for (const fit of document.querySelectorAll('.fit')) {
    const scroller = fit.querySelector('.fit-scroll');
    const view = fit.closest('.hlc').dataset.view;
    const hidden = scroller.scrollHeight - scroller.clientHeight;
    out.fits.push(`${view}:${hidden > 2 ? fit.querySelector('.fit-cue')?.textContent ?? 'NO CUE' : 'fits'}`);
    if (hidden > 2 && !fit.querySelector('.fit-cue')) say(`${view} hides ${hidden}px with no cue`);
  }
  const rows = [...document.querySelectorAll('.lane')].map((lane) => lane.getBoundingClientRect());
  rows.forEach((row, i) => {
    if (i && row.top < rows[i - 1].bottom - 1 && row.left === rows[i - 1].left) say(`lane ${i} overlaps the one above`);
  });
  for (const lane of document.querySelectorAll('.lane')) {
    const box = lane.getBoundingClientRect();
    for (const child of lane.children) {
      if (child.getBoundingClientRect().bottom > box.bottom + 1) say(`${lane.dataset.lane} spills out of its row`);
    }
  }
  for (const name of document.querySelectorAll('.sys b')) {
    if (name.scrollWidth > name.clientWidth + 1) say(`nav name cut: ${name.textContent}`);
  }
  for (const value of document.querySelectorAll('.meter b')) {
    if (value.getBoundingClientRect().height > 22) say(`meter value wrapped: ${value.textContent}`);
  }
  for (const pinned of document.querySelectorAll('.map .sumrow, .map .keyline')) {
    const card = pinned.closest('.card').getBoundingClientRect();
    if (pinned.getBoundingClientRect().bottom > card.bottom + 1) say(`${pinned.className} falls outside its card`);
  }
  return out;
};

const failures = [];
for (const width of [390, 728, 1100, 1440]) {
  for (const layout of ['native', 'scenes']) {
    for (const focus of ['Overview', 'Network']) {
      for (const fail of ['', '%5EH_', '.']) {
        const page = await browser.newPage({ viewport: { width, height: 900 } });
        await page.goto(`file://${here}/out/index.html?layout=${layout}&focus=${focus}&fail=${fail}`);
        await page.waitForTimeout(250);
        const { problems, fits } = await page.evaluate(measure);
        console.log(`${width} ${layout} ${focus} fail=${fail || 'none'}: ${problems.length ? problems.join('; ') : 'ok'}${fits.length ? ` [${fits.join(', ')}]` : ''}`);
        failures.push(...problems);
        await page.close();
      }
    }
  }
}
await browser.close();
assert.equal(failures.length, 0, `${failures.length} layout problems`);
console.log('layout check passed');
