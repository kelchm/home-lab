import { pathToFileURL } from 'node:url';

// Keep browser tooling outside the shipped plugins. Use an installed Playwright,
// or point PLAYWRIGHT_MODULE at a separate tooling checkout.
export const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
  : 'playwright');
export const launchOptions = {
  headless: true,
  ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}),
};
