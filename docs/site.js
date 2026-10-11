/* Two site-wide controls. The page works without this script.

   HDR limit (profile 14.3): a visible control to hold everything at SDR white.

   Theme: the pages follow the viewer's system setting through CSS alone. The control here lets a viewer choose
   light or dark for this site, and keeps the figures in step: each figure exists for a light and for a dark page
   (the same data and mapping, other furniture), named <name>_hdr.jpg and <name>_dark_hdr.jpg.

   Both choices are remembered in this browser only. */
(function () {
  var root = document.documentElement;
  function recall(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }   // private mode
  function remember(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* not remembered */ } }

  var style = document.createElement("style");
  style.textContent =
    "html.hdr-limit img, html.hdr-limit video, html.hdr-limit svg, html.hdr-limit canvas { dynamic-range-limit: standard !important; }" +
    ".site-switch { font: 600 12px/1 inherit; font-family: inherit; color: var(--ink, #e4e8ec); background: transparent;" +
    " border: 1px solid var(--line, #3a424b); border-radius: 4px; padding: 7px 10px; cursor: pointer; white-space: nowrap; }" +
    ".site-switch[aria-pressed='true'] { background: var(--warm, #e8b04a); color: var(--ground, #111316); border-color: var(--warm, #e8b04a); }" +
    ".site-switch:focus-visible { outline: 2px solid var(--tag, #8cc2e6); outline-offset: 2px; }" +
    ".site-switches { display: flex; gap: 8px; flex-wrap: wrap; }" +
    ".site-switch-bar { justify-content: flex-end; margin: 0 0 12px; }";
  document.head.appendChild(style);

  function button(title) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "site-switch"; b.title = title;
    return b;
  }

  /* ---- HDR limit */
  var LIMIT = "udeep-hdr-limit";
  var limited = recall(LIMIT) === "1";
  var limit = button("Hold every image and video on this site at ordinary (SDR) white");
  function applyLimit() {
    root.classList.toggle("hdr-limit", limited);
    limit.setAttribute("aria-pressed", String(limited));
    limit.textContent = limited ? "HDR limited to SDR" : "Limit HDR";
  }
  limit.addEventListener("click", function () { limited = !limited; remember(LIMIT, limited ? "1" : "0"); applyLimit(); });
  applyLimit();

  /* ---- theme. Only pages that carry the shared theme styles follow one; the device checks keep their dark ground. */
  var THEME = "udeep-theme";
  var themed = !!document.getElementById("theme");
  var system = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var theme = button("Light or dark page and figures. Auto follows this device's setting");
  var ORDER = ["auto", "light", "dark"];

  function chosen() { var t = root.getAttribute("data-theme"); return t === "light" || t === "dark" ? t : "auto"; }
  function dark() { var c = chosen(); return c === "dark" || (c === "auto" && !!system && system.matches); }
  function lightName(url) { return url.replace(/_dark_/, "_"); }
  function darkName(url) { return lightName(url).replace(/_(hdr\.jpg|sdr\.png|full\.png)$/, "_dark_$1"); }

  /* The figure for the page's theme. An <img> keeps the light file as its src (what a page's own HDR/SDR buttons
     set); the <source> before it holds the dark one, and its media says when it applies. */
  function figures() {
    var c = chosen();
    var media = c === "dark" ? "all" : c === "light" ? "not all" : "(prefers-color-scheme: dark)";
    document.querySelectorAll("img[data-themed]").forEach(function (img) {
      var source = img.parentNode.querySelector("source[data-dark]");
      if (!source) return;
      var name = darkName(img.getAttribute("src"));
      if (source.getAttribute("srcset") !== name) source.setAttribute("srcset", name);
      if (source.getAttribute("media") !== media) source.setAttribute("media", media);
    });
    document.querySelectorAll("a[data-themed]").forEach(function (a) {
      var href = a.getAttribute("href");
      var name = dark() ? darkName(href) : lightName(href);
      if (href !== name) a.setAttribute("href", name);
    });
  }
  function applyTheme() {
    theme.textContent = "Theme: " + chosen();
    figures();
  }
  if (themed) {
    var stored = recall(THEME);
    if (stored === "light" || stored === "dark") root.setAttribute("data-theme", stored);
    theme.addEventListener("click", function () {
      var next = ORDER[(ORDER.indexOf(chosen()) + 1) % ORDER.length];
      if (next === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", next);
      remember(THEME, next);
      applyTheme();
    });
    if (system && system.addEventListener) system.addEventListener("change", applyTheme);
    // a host page may set the theme on the root element itself
    if (window.MutationObserver) new MutationObserver(applyTheme).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    // a page's own HDR/SDR buttons put a light file name in place: bring the dark one into step after them
    document.addEventListener("click", function (e) { if (e.target.closest && e.target.closest(".toggle button")) figures(); });
  }

  function place() {
    var box = document.createElement("div");
    box.className = "site-switches";
    if (themed) box.appendChild(theme);
    box.appendChild(limit);
    var host = document.querySelector("header.top .wrap");
    if (host) { host.appendChild(box); }
    else {
      var main = document.querySelector("main");            // device-check pages have no header bar
      if (main) { box.className = "site-switches site-switch-bar"; main.insertBefore(box, main.firstChild); }
    }
    if (themed) applyTheme();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", place); else place();
})();
