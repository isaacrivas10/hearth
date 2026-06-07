function toggleTheme() {
    const el = document.documentElement;
    const next = el.dataset.theme === "dark" ? "light" : "dark";
    el.dataset.theme = next;
    localStorage.setItem("theme", next);
}
window.toggleTheme = toggleTheme;
