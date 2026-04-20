(function () {
  function normalize(path) {
    return path.replace(/\/+$/, "").replace(/\/index\.html$/, "");
  }

  function syncTreeNavigation() {
    var nav = document.querySelector("[data-tree-nav]");
    if (!nav) return;

    var current = normalize(window.location.pathname);
    var links = Array.prototype.slice.call(nav.querySelectorAll("a[href]"));
    var active = null;

    links.forEach(function (link) {
      var href = link.getAttribute("href");
      if (!href || href.indexOf("http") === 0 || href.indexOf("#") === 0) return;
      var url = new URL(href, window.location.href);
      var samePath = normalize(url.pathname) === current;
      var sameHash = !url.hash || url.hash === window.location.hash;
      if (samePath && sameHash && !active) active = link;
    });

    if (!active) {
      active = links.find(function (link) {
        var url = new URL(link.getAttribute("href"), window.location.href);
        return normalize(url.pathname) === current;
      });
    }

    if (active) {
      active.classList.add("is-active");
      var parent = active.parentElement;
      while (parent) {
        if (parent.tagName && parent.tagName.toLowerCase() === "details") {
          parent.open = true;
        }
        parent = parent.parentElement;
      }
    }
  }

  function syncHeadingNavigation() {
    var nav = document.querySelector("[data-tree-nav]");
    var headings = Array.prototype.slice.call(document.querySelectorAll(".content h2[id], .content h3[id], .content h4[id]"));
    if (!nav || headings.length === 0 || !("IntersectionObserver" in window)) return;

    var linksByHash = {};
    Array.prototype.slice.call(nav.querySelectorAll("a[href*='#']")).forEach(function (link) {
      var url = new URL(link.getAttribute("href"), window.location.href);
      if (normalize(url.pathname) === normalize(window.location.pathname)) {
        linksByHash[url.hash] = link;
      }
    });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var link = linksByHash["#" + entry.target.id];
        if (!link) return;
        Object.keys(linksByHash).forEach(function (hash) {
          linksByHash[hash].classList.remove("is-current-section");
        });
        link.classList.add("is-current-section");
        var parent = link.parentElement;
        while (parent) {
          if (parent.tagName && parent.tagName.toLowerCase() === "details") parent.open = true;
          parent = parent.parentElement;
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px", threshold: 0.01 });

    headings.forEach(function (heading) {
      observer.observe(heading);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    syncTreeNavigation();
    syncHeadingNavigation();
  });
})();
