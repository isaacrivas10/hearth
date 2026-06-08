// prefs.js — appearance preference setters (localStorage + data-attrs on <html>).
// theme-toggle.js owns toggleTheme() for the header button.
// This file adds setTheme() (value-setter for preference tiles) + the other dims.

function setTheme(v) {
    if (["light", "dark"].indexOf(v) === -1) return;
    try {
        var dark = v === "dark";
        document.documentElement.dataset.theme = v;
        document.documentElement.classList.toggle("wa-dark", dark);
        document.documentElement.classList.toggle("wa-light", !dark);
        localStorage.setItem("theme", v);
    } catch (e) {}
}

function setDensity(v) {
    if (["compact", "balanced", "airy"].indexOf(v) === -1) return;
    try {
        document.documentElement.dataset.density = v;
        localStorage.setItem("density", v);
    } catch (e) {}
}

function setSidebarTone(v) {
    if (["match", "subtle", "branded"].indexOf(v) === -1) return;
    try {
        document.documentElement.dataset.sidebarTone = v;
        localStorage.setItem("sidebar-tone", v);
    } catch (e) {}
}

function setRadius(v) {
    if (["rounded", "sharp"].indexOf(v) === -1) return;
    try {
        document.documentElement.dataset.radius = v;
        localStorage.setItem("radius", v);
    } catch (e) {}
}

function resetPreferences() {
    if (!confirm("Reset all appearance preferences to defaults?")) return;
    try {
        ["theme", "density", "sidebar-tone", "radius"].forEach(function (k) {
            localStorage.removeItem(k);
        });
    } catch (e) {}
    location.reload();
}

window.setTheme = setTheme;
window.setDensity = setDensity;
window.setSidebarTone = setSidebarTone;
window.setRadius = setRadius;
window.resetPreferences = resetPreferences;
