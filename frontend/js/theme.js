// Theme state: dark (default) or light, persisted per browser. CSS handles
// the 2D chrome via :root[data-theme]; the 3D palette goes through
// viewport.setTheme because THREE materials can't read CSS custom properties.
// index.html has a pre-paint inline script that restores the attribute early
// so a stored light theme never flashes dark.

import * as viewport from "./viewport.js";

const STORAGE_KEY = "agentcad.theme";

const SCENE_THEMES = {
  // diffAdded/diffRemoved are the proposal geometry-diff overlay colors —
  // --ok and --err from the CSS tokens, which THREE cannot read itself.
  dark: {
    background: 0x17181b,
    gridMajor: 0x2c2f36,
    gridMinor: 0x22242a,
    edge: 0x0d0e10,
    diffAdded: 0x6fbf8f,
    diffRemoved: 0xe0655c,
  },
  light: {
    background: 0xe9ebef,
    gridMajor: 0xc4cad2,
    gridMinor: 0xd9dde2,
    edge: 0x2f353c,
    diffAdded: 0x2e8a57,
    diffRemoved: 0xc2413a,
  },
};

let active = "dark";

export function current() {
  return active;
}

export function toggle() {
  apply(active === "light" ? "dark" : "light");
}

export function init() {
  document.getElementById("theme-btn").addEventListener("click", toggle);
  let stored = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch {
    stored = document.documentElement.dataset.theme; // private mode fallback
  }
  apply(stored === "light" ? "light" : "dark");
}

function apply(name) {
  active = name;
  if (name === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  try {
    localStorage.setItem(STORAGE_KEY, name);
  } catch {
    // no persistence available; the in-page toggle still works
  }
  viewport.setTheme(SCENE_THEMES[name]);
  const btn = document.getElementById("theme-btn");
  if (btn) {
    const label = name === "light" ? "Switch to dark theme" : "Switch to light theme";
    btn.textContent = name === "light" ? "☾" : "☀";
    btn.title = label;
    btn.setAttribute("aria-label", label);
  }
}
