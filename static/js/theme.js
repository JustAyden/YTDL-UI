// Material 3 Expressive dynamic color: every color role is generated from one seed color
// with Google's Material Color Utilities (2025 color spec), for light and dark, at the
// chosen contrast level and scheme style. No DOM access at import time (also used by
// packaging/gen_theme_css.mjs to bake the default theme into static/css/theme.css).
import * as MCU from "../vendor/material-color-utilities.js";

export const DEFAULT_SEED = "#22d3ee";
export const DEFAULT_STYLE = "tonal_spot";

export const STYLES = {
  tonal_spot: { label: "Tonal", ctor: "SchemeTonalSpot", note: "Calm, Android's default" },
  vibrant: { label: "Vibrant", ctor: "SchemeVibrant", note: "Saturated, punchy" },
  expressive: { label: "Expressive", ctor: "SchemeExpressive", note: "Playful hue shifts" },
  fidelity: { label: "Faithful", ctor: "SchemeFidelity", note: "Stays close to the color" },
  neutral: { label: "Neutral", ctor: "SchemeNeutral", note: "Barely tinted" },
  monochrome: { label: "Mono", ctor: "SchemeMonochrome", note: "Grayscale" },
};

export const PRESETS = [
  { name: "Cyan", hex: "#22d3ee" },
  { name: "Blue", hex: "#3b82f6" },
  { name: "Violet", hex: "#6750a4" },
  { name: "Pink", hex: "#e8508b" },
  { name: "Red", hex: "#e5484d" },
  { name: "Orange", hex: "#f97316" },
  { name: "Yellow", hex: "#eab308" },
  { name: "Green", hex: "#22a55a" },
  { name: "Teal", hex: "#0f9d8f" },
];

export const CONTRASTS = { standard: 0, medium: 0.5, high: 1 };

const ROLES = new MCU.MaterialDynamicColors().allColors;

export function isHex(v) { return /^#[0-9a-f]{6}$/i.test(v || ""); }

export function schemeVars(seedHex, style = DEFAULT_STYLE, contrast = 0, dark = false) {
  const spec = STYLES[style] || STYLES[DEFAULT_STYLE];
  const Ctor = MCU[spec.ctor] || MCU.SchemeTonalSpot;
  const scheme = new Ctor(MCU.Hct.fromInt(MCU.argbFromHex(isHex(seedHex) ? seedHex : DEFAULT_SEED)), dark, contrast, "2025");
  const out = [];
  for (const c of ROLES) out.push(`--md-sys-color-${c.name.replace(/_/g, "-")}:${MCU.hexFromArgb(c.getArgb(scheme))}`);
  out.push("--md-sys-color-shadow:#000", "--md-sys-color-scrim:#000");
  return out.join(";");
}

export function themeCss(seedHex, style, contrastName = "standard") {
  const c = CONTRASTS[contrastName] ?? 0;
  const light = schemeVars(seedHex, style, c, false);
  const dark = schemeVars(seedHex, style, c, true);
  return `:root{${light}}` +
    `@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){${dark};color-scheme:dark}}` +
    `:root[data-theme="dark"]{${dark};color-scheme:dark}`;
}

// ── browser side ────────────────────────────────────────────────────────
const KEY = "ytdlui.appearance";
const CSS_KEY = "ytdlui.theme.css";

export function loadAppearance() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

export function saveAppearance(a) {
  try { localStorage.setItem(KEY, JSON.stringify(a)); } catch { /* private mode */ }
}

/** Apply mode/color/style/contrast/motion. `defaults` = server-chosen accent + style. */
export function applyAppearance(a, defaults = {}) {
  const root = document.documentElement;
  const mode = a.mode || "system";
  if (mode === "system") delete root.dataset.theme; else root.dataset.theme = mode;
  if (a.reduceMotion) root.dataset.motion = "reduced"; else delete root.dataset.motion;
  const seed = a.seed || defaults.seed || DEFAULT_SEED;
  const style = a.style || defaults.style || DEFAULT_STYLE;
  const contrast = a.contrast || "standard";
  const isDefault = seed.toLowerCase() === DEFAULT_SEED && style === DEFAULT_STYLE && contrast === "standard";
  let el = document.getElementById("dyn-theme");
  if (isDefault) {                       // baked into theme.css already
    el?.remove();
    try { localStorage.removeItem(CSS_KEY); } catch { /* ignore */ }
  } else {
    const css = themeCss(seed, style, contrast);
    if (!el) { el = document.createElement("style"); el.id = "dyn-theme"; document.head.appendChild(el); }
    el.textContent = css;
    try { localStorage.setItem(CSS_KEY, css); } catch { /* ignore */ }
  }
  const meta = document.querySelectorAll('meta[name="theme-color"]');
  requestAnimationFrame(() => {
    const bg = getComputedStyle(root).getPropertyValue("--md-sys-color-surface").trim();
    meta.forEach((m) => m.setAttribute("content", bg));
  });
}

export function swatchColor(hex, style = DEFAULT_STYLE) {
  const spec = STYLES[style] || STYLES[DEFAULT_STYLE];
  const scheme = new MCU[spec.ctor](MCU.Hct.fromInt(MCU.argbFromHex(hex)), false, 0, "2025");
  const m = new MCU.MaterialDynamicColors();
  return {
    primary: MCU.hexFromArgb(m.primary().getArgb(scheme)),
    secondary: MCU.hexFromArgb(m.secondaryContainer().getArgb(scheme)),
    tertiary: MCU.hexFromArgb(m.tertiaryContainer().getArgb(scheme)),
  };
}
