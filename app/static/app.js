(function () {
  // Mavzu (yorug'/qorong'i)
  var btn = document.getElementById("theme-toggle");
  function apply(t) {
    document.documentElement.setAttribute("data-theme", t);
    if (btn) btn.textContent = t === "dark" ? "☀️" : "🌙";
  }
  var saved = null;
  try { saved = localStorage.getItem("theme"); } catch (e) {}
  apply(saved || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  if (btn) btn.addEventListener("click", function () {
    var t = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", t); } catch (e) {}
    apply(t);
  });

  // Nusxalash tugmalari
  document.querySelectorAll("[data-copy]").forEach(function (el) {
    el.addEventListener("click", function () {
      var text = document.querySelector(el.getAttribute("data-copy")).textContent.trim();
      navigator.clipboard.writeText(text).then(function () {
        var old = el.textContent; el.textContent = "Nusxalandi ✓";
        setTimeout(function () { el.textContent = old; }, 1500);
      });
    });
  });

  // Tasdiqlash so'raydigan formalar
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!confirm(f.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Jonli yangilanish: <div id="live" data-refresh="5000"> ichini davriy yangilaydi
  var live = document.getElementById("live");
  if (live && live.dataset.refresh) {
    setInterval(function () {
      if (document.hidden) return;
      fetch(location.href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) {
          if (!html) return;
          var doc = new DOMParser().parseFromString(html, "text/html");
          var fresh = doc.getElementById("live");
          if (fresh) live.innerHTML = fresh.innerHTML;
        }).catch(function () {});
    }, parseInt(live.dataset.refresh, 10));
  }
})();
