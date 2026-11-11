// Tabs, sub-use list per purpose group, busy state on submit, and the findings filter.
(function () {
  // tabs on the start page
  var tabs = document.querySelectorAll(".tab");
  function show(name) {
    tabs.forEach(function (t) {
      var on = t.dataset.tab === name;
      t.setAttribute("aria-selected", on ? "true" : "false");
      document.getElementById("tab-" + t.dataset.tab).hidden = !on;
    });
    try { localStorage.setItem("ubbl-tab", name); } catch (e) { /* storage blocked: fine */ }
  }
  tabs.forEach(function (t) { t.addEventListener("click", function () { show(t.dataset.tab); }); });
  if (tabs.length) {
    var saved = null;
    try { saved = localStorage.getItem("ubbl-tab"); } catch (e) { /* ignore */ }
    if (saved && document.getElementById("tab-" + saved)) show(saved);
  }

  // only the sub-uses of the chosen purpose group
  var group = document.getElementById("group"), sub = document.getElementById("sub_use");
  function syncSub() {
    var g = group.value, keep = sub.dataset.selected || sub.value, any = false;
    Array.prototype.forEach.call(sub.options, function (o) {
      if (!o.dataset.group) return;
      var on = o.dataset.group === g;
      o.hidden = !on; o.disabled = !on;
      if (on) any = true;
    });
    sub.value = "";
    Array.prototype.forEach.call(sub.options, function (o) { if (!o.disabled && o.value === keep) sub.value = keep; });
    sub.dataset.selected = "";
    sub.disabled = !any;
  }
  if (group && sub) { group.addEventListener("change", syncSub); syncSub(); }

  // a check can take minutes: show it is running
  document.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function () {
      var b = f.querySelector("button[type=submit]"), busy = f.querySelector(".busy");
      if (b) b.disabled = true;
      if (busy) busy.hidden = false;
    });
  });

  // findings: severity buttons and text filter
  var rows = document.querySelectorAll(".findings tbody tr");
  if (!rows.length) return;
  var stats = document.querySelectorAll(".stat"), search = document.querySelector(".search");
  var empty = document.querySelector(".empty"), sev = null;
  function apply() {
    var q = (search.value || "").toLowerCase(), shown = 0;
    rows.forEach(function (r) {
      var on = (!sev || r.dataset.sev === sev) && (!q || r.textContent.toLowerCase().indexOf(q) >= 0);
      r.hidden = !on;
      if (on) shown++;
    });
    empty.hidden = shown > 0;
  }
  stats.forEach(function (s) {
    s.addEventListener("click", function () {
      sev = sev === s.dataset.filter ? null : s.dataset.filter;
      stats.forEach(function (x) { x.setAttribute("aria-pressed", x.dataset.filter === sev ? "true" : "false"); });
      apply();
    });
  });
  search.addEventListener("input", apply);
})();
