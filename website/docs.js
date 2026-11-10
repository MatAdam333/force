// Force docs: the copy button on the install line, and the contents list marking the section you are reading.
(function () {
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var el = document.getElementById(b.getAttribute("data-copy"));
      var text = el.textContent.replace(/^\s*\$\s*/, "").trim();
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(text).then(function () {
        b.textContent = "Copied";
        setTimeout(function () { b.textContent = "Copy"; }, 1600);
      });
    });
  });

  var links = Array.prototype.slice.call(document.querySelectorAll(".toc a"));
  var sections = links.map(function (a) { return document.querySelector(a.getAttribute("href")); });
  function mark() {
    var y = window.scrollY + 120, cur = 0;
    sections.forEach(function (s, i) { if (s && s.offsetTop <= y) cur = i; });
    if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 4) cur = sections.length - 1;
    links.forEach(function (a, i) {
      if (i !== cur) { a.removeAttribute("aria-current"); return; }
      if (a.hasAttribute("aria-current")) return;
      a.setAttribute("aria-current", "true");
      var toc = a.parentNode;   // on a phone the list is one scrolling row: keep the current one in it
      if (toc.scrollWidth > toc.clientWidth) toc.scrollLeft = a.offsetLeft - toc.clientWidth / 3;
    });
  }
  window.addEventListener("scroll", mark, { passive: true });
  mark();
})();
