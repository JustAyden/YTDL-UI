// Runs before first paint: apply the visitor's saved theme mode, motion preference and
// generated color scheme so the page never flashes the default colors.
(function () {
  try {
    var root = document.documentElement;
    var a = JSON.parse(localStorage.getItem("ytdlui.appearance") || "{}");
    if (a.mode === "light" || a.mode === "dark") root.setAttribute("data-theme", a.mode);
    if (a.reduceMotion) root.setAttribute("data-motion", "reduced");
    var css = localStorage.getItem("ytdlui.theme.css");
    if (css) {
      var el = document.createElement("style");
      el.id = "dyn-theme";
      el.textContent = css;
      document.head.appendChild(el);
    }
  } catch (e) { /* storage blocked: defaults apply */ }
})();
