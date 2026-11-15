// Material 3 Expressive shape library, approximated as CSS clip-path polygons.
// Every shape uses the same number of points starting at the same angle, so the
// browser can morph smoothly between any two of them (clip-path transitions).

const N = 96;

const radial = {
  circle: () => 1,
  oval: (t) => { const a = 1, b = 0.74; return (a * b) / Math.hypot(b * Math.cos(t), a * Math.sin(t)); },
  pill: (t) => { const n = 5, a = 1, b = 0.6; return (Math.abs(Math.cos(t) / a) ** n + Math.abs(Math.sin(t) / b) ** n) ** (-1 / n); },
  square: (t) => { const n = 5.5; return (Math.abs(Math.cos(t)) ** n + Math.abs(Math.sin(t)) ** n) ** (-1 / n); },
  cookie4: (t) => 0.86 + 0.14 * Math.cos(4 * t),
  cookie6: (t) => 0.88 + 0.12 * Math.cos(6 * t),
  cookie9: (t) => 0.9 + 0.1 * Math.cos(9 * t),
  cookie12: (t) => 0.92 + 0.08 * Math.cos(12 * t),
  sunny: (t) => 0.86 + 0.14 * Math.cos(8 * t),
  clover4: (t) => 0.62 + 0.38 * Math.abs(Math.cos(2 * t)) ** 0.75,
  clover8: (t) => 0.78 + 0.22 * Math.abs(Math.cos(4 * t)) ** 0.8,
  softBurst: (t) => 0.8 + 0.2 * ((1 + Math.cos(10 * t)) / 2) ** 1.6,
  flower: (t) => 0.74 + 0.26 * ((1 + Math.cos(8 * t)) / 2) ** 0.6,
  pentagon: (t) => {
    const k = 5, seg = (2 * Math.PI) / k;
    const a = ((t + Math.PI / 2) % seg + seg) % seg - seg / 2;
    return 0.7 * (Math.cos(Math.PI / k) / Math.cos(a)) + 0.3 * 0.92;
  },
  gem: (t) => {
    const k = 6, seg = (2 * Math.PI) / k;
    const a = ((t + Math.PI / 2) % seg + seg) % seg - seg / 2;
    return 0.75 * (Math.cos(Math.PI / k) / Math.cos(a)) + 0.25 * 0.95;
  },
};

export const SHAPES = Object.keys(radial);

export function polygon(name, rotate = 0) {
  const f = radial[name] || radial.circle;
  const pts = [];
  let max = 0;
  const rs = [];
  for (let i = 0; i < N; i++) {
    const t = (i / N) * 2 * Math.PI - Math.PI / 2;
    const r = f(t);
    rs.push([t, r]);
    if (r > max) max = r;
  }
  for (const [t, r] of rs) {
    const rr = (r / max) * 50;
    const a = t + rotate;
    pts.push(`${(50 + rr * Math.cos(a)).toFixed(2)}% ${(50 + rr * Math.sin(a)).toFixed(2)}%`);
  }
  return `polygon(${pts.join(",")})`;
}

/** Inject CSS custom properties --shape-<name> and the loading-indicator keyframes. */
export function installShapes() {
  if (document.getElementById("md-shapes")) return;
  const vars = SHAPES.map((s) => `--shape-${s}:${polygon(s)}`).join(";");
  // M3 Expressive loading indicator: soft burst → cookie 9 → pentagon → pill → sunny → cookie 4 → oval
  const seq = ["softBurst", "cookie9", "pentagon", "pill", "sunny", "cookie4", "oval"];
  const frames = seq.map((s, i) => `${((i / seq.length) * 100).toFixed(2)}%{clip-path:${polygon(s)}}`).join("") +
    `100%{clip-path:${polygon(seq[0])}}`;
  const el = document.createElement("style");
  el.id = "md-shapes";
  el.textContent = `:root{${vars}}@keyframes md-loader-morph{${frames}}`;
  document.head.appendChild(el);
}
