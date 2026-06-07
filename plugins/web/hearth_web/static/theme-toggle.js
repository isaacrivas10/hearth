// theme-toggle.js — toggles Hearth data-theme + Web Awesome wa-dark/wa-light.
function toggleTheme() {
  var el = document.documentElement;
  var dark = el.dataset.theme !== "dark";
  el.dataset.theme = dark ? "dark" : "light";
  el.classList.toggle("wa-dark", dark);
  el.classList.toggle("wa-light", !dark);
  localStorage.setItem("theme", dark ? "dark" : "light");
}
window.toggleTheme = toggleTheme;
