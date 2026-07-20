// Mirror of kanidm's pkg/style.js (sets data-bs-theme from the OS colour scheme,
// live on change and after htmx swaps) with ONE addition: keep the
// <meta name="theme-color"> in sync with the mode, so the mobile browser chrome
// — the Safari bars and the notch / home-indicator insets — matches the theme
// instead of kanidm's hardcoded white. A meta tag can't be changed from CSS, so
// this small JS override is the only lever.
//
// This REPLACES kanidm's own style.js (mounted at /hpkg/style.js). Keep the
// data-bs-theme logic identical to upstream and re-check on kanidm upgrades — if
// upstream changes its theming JS, re-sync this file or the light/dark toggle
// breaks. Verified against kanidm v1.10.3.
function getPreferredTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function updateColourScheme() {
  const theme = getPreferredTheme();
  document.documentElement.setAttribute("data-bs-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#13172a" : "#f7f8fc");
}

// kanidm ships hidden #password/#totp decoy inputs (class .d-none) on the login
// page so a password manager can autofill them in the background on the username
// step. iOS Safari renders those decoys despite display:none — a phantom input
// pinned to the bottom of the viewport. Converting them to type=hidden makes them
// unrenderable on every browser while still submitting their (empty) value, so
// the form is unchanged. Guarded on .d-none so real credential inputs on later
// steps are never touched. Trade-off: no background password/OTP pre-fill on the
// username step (username autofill and the actual password/2FA step are fine).
function neutraliseAutofillDecoys() {
  document.querySelectorAll("#password.d-none, #totp.d-none").forEach((el) => {
    if (el.type !== "hidden") el.type = "hidden";
  });
}

updateColourScheme();
neutraliseAutofillDecoys();
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", updateColourScheme);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateColourScheme);
document.body.addEventListener("htmx:afterOnLoad", function () {
  updateColourScheme();
  neutraliseAutofillDecoys();
});
