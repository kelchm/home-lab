import { vi } from 'vitest';

class NoopObserver {
  public observe() {}
  public unobserve() {}
  public disconnect() {}
}

vi.stubGlobal('ResizeObserver', NoopObserver);
vi.stubGlobal('IntersectionObserver', NoopObserver);
window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })) as never;
window.scrollTo = (() => {}) as never;

// Grafana's Table measures text on a canvas, which jsdom does not implement.
HTMLCanvasElement.prototype.getContext = (() => ({ font: '', measureText: (text: string) => ({ width: text.length * 7 }) })) as never;
const computedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = ((element: Element) => computedStyle(element)) as typeof window.getComputedStyle;
