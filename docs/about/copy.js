document.getElementById("copy").addEventListener("click", function () {
  var text = document.getElementById("template").textContent, note = document.getElementById("copied");
  function fallback() {
    var range = document.createRange(); range.selectNodeContents(document.getElementById("template"));
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    note.textContent = "Selected. Press Ctrl+C or ⌘C to copy.";
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { note.textContent = "Copied."; }, fallback);
  } else { fallback(); }
});
