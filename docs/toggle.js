(function () {
  // every toggle button names the image it controls and the file to show; switching is always user-started
  document.querySelectorAll(".toggle button").forEach(btn => btn.addEventListener("click", () => {
    document.getElementById(btn.dataset.img).src = btn.dataset.src;
    document.querySelectorAll(`.toggle button[data-img="${btn.dataset.img}"]`).forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
  }));
})();
