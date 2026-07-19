// Mounted over kanidm's /pkg/style.js (kanidm 1.10.3). Mirrors the stock
// colour-mode toggle exactly — set data-bs-theme from prefers-color-scheme, live
// on OS change and after every htmx swap — and adds one thing: keep the
// <meta name="theme-color"> in sync so the mobile browser chrome (Safari's
// URL/tool bars) matches the theme instead of staying white.
//
// This is the ONLY way to reach the theme-color meta (CSS can't), so it replaces
// the stock file. Re-check against upstream style.js on kanidm upgrades.
function getPreferredTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function updateColourScheme() {
  const theme = getPreferredTheme();
  document.documentElement.setAttribute("data-bs-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#13172a" : "#f7f8fc");
}
updateColourScheme();
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", updateColourScheme);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateColourScheme);
document.body.addEventListener("htmx:afterOnLoad", updateColourScheme);
