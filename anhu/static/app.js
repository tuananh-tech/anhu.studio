// JS tối thiểu: đếm học viên trực tiếp (chỉ hiển thị), thêm học viên từ hồ sơ, tự submit, xác nhận, lưu bộ lọc.
(function () {
  function splitNames(s) {
    return (s || "").split(/[,;\n\r]+/).map(function (x) { return x.replace(/\s+/g, " ").trim(); }).filter(Boolean);
  }
  function refresh(picker) {
    var n = picker.querySelectorAll('input[type=checkbox]:checked').length;
    var ex = picker.querySelector('[data-extra]');
    if (ex) n += splitNames(ex.value).length;
    var c = picker.querySelector('[data-count]');
    if (c) c.textContent = n + " HV";
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-picker]").forEach(function (p) {
      p.addEventListener("input", function () { refresh(p); });
      p.addEventListener("change", function (e) {
        if (e.target.matches("[data-add-student]") && e.target.value) {
          var v = e.target.value, name = v.split("|").slice(1).join("|");
          var lab = document.createElement("label");
          lab.className = "chip";
          var cb = document.createElement("input");
          cb.type = "checkbox"; cb.name = (p.dataset.prefix || "") + "present"; cb.value = v; cb.checked = true;
          lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + name));
          p.querySelector("[data-chips]").appendChild(lab);
          e.target.value = "";
        }
        refresh(p);
      });
      refresh(p);
    });
    document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
      el.addEventListener("change", function () { el.form.submit(); });
    });
    document.querySelectorAll("form[data-confirm]").forEach(function (f) {
      f.addEventListener("submit", function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
    });
    document.querySelectorAll("[data-checkall]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        document.querySelectorAll('input[name="' + cb.dataset.checkall + '"]').forEach(function (x) { x.checked = cb.checked; });
      });
    });
    document.querySelectorAll("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });
    // Lưu bộ lọc thường dùng (localStorage, chỉ trên máy này)
    var box = document.querySelector("[data-saved-filters]");
    if (box) {
      var key = "anhu.filters." + location.pathname;
      var saved = JSON.parse(localStorage.getItem(key) || "[]");
      function render() {
        box.innerHTML = "";
        saved.forEach(function (f, i) {
          var a = document.createElement("a"); a.href = location.pathname + f.q; a.textContent = f.name; a.className = "btn small";
          var x = document.createElement("button"); x.type = "button"; x.textContent = "×"; x.className = "btn small";
          x.title = "Xóa bộ lọc đã lưu";
          x.addEventListener("click", function () { saved.splice(i, 1); localStorage.setItem(key, JSON.stringify(saved)); render(); });
          box.appendChild(a); box.appendChild(x);
        });
      }
      render();
      var btn = document.querySelector("[data-save-filter]");
      if (btn) btn.addEventListener("click", function () {
        var name = window.prompt("Tên bộ lọc:");
        if (!name) return;
        saved.push({ name: name, q: location.search });
        localStorage.setItem(key, JSON.stringify(saved));
        render();
      });
    }
  });
})();
