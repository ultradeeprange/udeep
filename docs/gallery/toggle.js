document.querySelectorAll(".toggle button").forEach(btn => btn.addEventListener("click", () => {
  document.getElementById(btn.dataset.img).src = btn.dataset.src;
  document.getElementById(btn.dataset.img.replace("-img", "-file")).href = btn.dataset.src;
  document.querySelectorAll(`.toggle button[data-img="${btn.dataset.img}"]`).forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
}));
