/* research/static/ui.js — shell behaviour and the small shared helpers.

   window.ui.toast(title, {tone, description, action: {label, onClick}, duration})
   window.ui.confirm({title, body, confirm, cancel, tone}) -> Promise<boolean>
   window.ui.ago(date) -> "6 min ago"

   Icons for toasts come from <template id="ui-icon-…"> elements rendered by _base.html,
   so the SVG paths live in one place (_icons.html). */
(function () {
  var root = document.documentElement;
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }
  };
  var mobile = function () { return window.matchMedia('(max-width: 767px)').matches; };
  var iconHTML = function (name) {
    var t = document.getElementById('ui-icon-' + name);
    return t ? t.innerHTML : '';
  };

  // ── Sidebar: collapse on desktop, drawer on phones; Ctrl/⌘+B toggles ─────────
  function toggleNav() {
    if (mobile()) { root.classList.toggle('drawer-open'); return; }
    var on = root.classList.toggle('collapsed');
    store.set('sidebar.collapsed', on ? '1' : null);
  }
  document.getElementById('navtoggle')?.addEventListener('click', toggleNav);
  document.querySelector('.drawer-overlay')?.addEventListener('click', function () { root.classList.remove('drawer-open'); });
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'b' || e.key === 'B')) { e.preventDefault(); toggleNav(); }
    if (e.key === 'Escape') { closeMenus(); root.classList.remove('drawer-open'); }
  });

  // ── Menus (theme) ────────────────────────────────────────────────────────────
  function closeMenus() { document.querySelectorAll('.menu').forEach(function (m) { m.hidden = true; }); }
  document.querySelectorAll('[data-menu]').forEach(function (btn) {
    var menu = document.getElementById(btn.dataset.menu);
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = menu.hidden;
      closeMenus();
      if (open) {
        var r = btn.getBoundingClientRect();
        menu.style.top = (r.bottom + 6 + window.scrollY) + 'px';
        menu.style.right = (document.documentElement.clientWidth - r.right) + 'px';
        menu.hidden = false;
      }
    });
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('.menu')) closeMenus(); });

  // ── Theme: Light / Dark / System ─────────────────────────────────────────────
  var dark = window.matchMedia('(prefers-color-scheme: dark)');
  function applyTheme(choice) {
    root.dataset.themeChoice = choice;
    root.dataset.theme = choice === 'system' ? (dark.matches ? 'dark' : 'light') : choice;
    document.querySelectorAll('[data-theme-choice]').forEach(function (b) {
      b.setAttribute('aria-checked', b.dataset.themeChoice === choice ? 'true' : 'false');
    });
    var icon = document.querySelector('#thememenu-btn .theme-icon');
    if (icon) icon.innerHTML = iconHTML(choice === 'light' ? 'sun' : choice === 'dark' ? 'moon' : 'monitor');
  }
  document.querySelectorAll('[data-theme-choice]').forEach(function (b) {
    b.addEventListener('click', function () {
      var c = b.dataset.themeChoice;
      store.set('theme', c === 'system' ? null : c);
      applyTheme(c);
      closeMenus();
    });
  });
  dark.addEventListener?.('change', function () { if (root.dataset.themeChoice === 'system') applyTheme('system'); });
  applyTheme(root.dataset.themeChoice || 'system');

  // ── Technical details switch ─────────────────────────────────────────────────
  var adv = document.getElementById('advtoggle');
  function showAdv() {
    var on = root.dataset.advanced === '1';
    if (!adv) return;
    adv.setAttribute('aria-pressed', on ? 'true' : 'false');
    var s = adv.querySelector('.adv-state');
    if (s) s.textContent = on ? 'on' : 'off';
  }
  adv?.addEventListener('click', function () {
    if (root.dataset.advanced === '1') delete root.dataset.advanced; else root.dataset.advanced = '1';
    store.set('advanced', root.dataset.advanced === '1' ? '1' : null);
    showAdv();
  });
  showAdv();

  // ── Toasts ───────────────────────────────────────────────────────────────────
  var stack = document.querySelector('.toast-stack');
  var TONE_ICON = {success: 'circle-check', danger: 'circle-x', warning: 'triangle-alert', info: 'info'};
  function toast(title, opts) {
    opts = opts || {};
    if (!stack) return;
    var tone = opts.tone || 'success';
    var el = document.createElement('div');
    el.className = 'toast toast-' + tone;
    el.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    el.innerHTML = iconHTML(TONE_ICON[tone] || 'info') + '<div class="toast-text"><div class="toast-title"></div>'
      + (opts.description ? '<div class="toast-desc"></div>' : '') + '</div>';
    el.querySelector('.toast-title').textContent = title;
    if (opts.description) el.querySelector('.toast-desc').textContent = opts.description;
    if (opts.action) {
      var a = document.createElement('button');
      a.className = 'btn-sm btn-ghost';
      a.textContent = opts.action.label;
      a.addEventListener('click', function () { opts.action.onClick(); remove(); });
      el.appendChild(a);
    }
    var close = document.createElement('button');
    close.className = 'btn-sm btn-ghost btn-icon';
    close.setAttribute('aria-label', 'Dismiss');
    close.innerHTML = iconHTML('x');
    close.addEventListener('click', remove);
    el.appendChild(close);
    stack.appendChild(el);
    var timer = setTimeout(remove, opts.duration || 6000);
    function remove() { clearTimeout(timer); el.remove(); }
    return remove;
  }

  // ── Confirm dialog (replaces window.confirm) ─────────────────────────────────
  function confirmDialog(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var back = document.createElement('div');
      back.className = 'dialog-backdrop';
      back.innerHTML = '<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-title">'
        + '<h2 id="dlg-title"></h2><p class="dialog-body"></p><div class="dialog-foot">'
        + '<button type="button" class="dlg-cancel"></button>'
        + '<button type="button" class="dlg-ok"></button></div></div>';
      back.querySelector('#dlg-title').textContent = opts.title || 'Are you sure?';
      back.querySelector('.dialog-body').textContent = opts.body || '';
      var ok = back.querySelector('.dlg-ok'), cancel = back.querySelector('.dlg-cancel');
      ok.textContent = opts.confirm || 'Continue';
      ok.className = 'dlg-ok ' + (opts.tone === 'danger' ? 'btn-danger' : 'btn-primary');
      cancel.textContent = opts.cancel || 'Cancel';
      var done = function (v) { back.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
      var onKey = function (e) { if (e.key === 'Escape') done(false); };
      ok.addEventListener('click', function () { done(true); });
      cancel.addEventListener('click', function () { done(false); });
      back.addEventListener('click', function (e) { if (e.target === back) done(false); });
      document.addEventListener('keydown', onKey);
      document.body.appendChild(back);
      cancel.focus();       // the safe choice is the default, as in the reference app
    });
  }

  // ── Relative times ───────────────────────────────────────────────────────────
  function ago(d) {
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (isNaN(s)) return '—';
    if (s < 45) return 'just now';
    var m = Math.round(s / 60); if (m < 60) return m + ' min ago';
    var h = Math.round(m / 60); if (h < 24) return h + ' h ago';
    var days = Math.round(h / 24); return days + ' day' + (days === 1 ? '' : 's') + ' ago';
  }
  function refreshTimes() {
    document.querySelectorAll('time[data-ago]').forEach(function (t) {
      var d = new Date(t.getAttribute('datetime'));
      if (!isNaN(d)) { t.textContent = ago(d); if (!t.title) t.title = t.getAttribute('datetime').replace('T', ' '); }
    });
  }
  refreshTimes();
  setInterval(refreshTimes, 60000);

  window.ui = {toast: toast, confirm: confirmDialog, ago: ago};

  // A message carried over a page change (e.g. "Stories written" after a run).
  try {
    var flash = sessionStorage.getItem('ui.flash');
    if (flash) {
      sessionStorage.removeItem('ui.flash');
      var f = JSON.parse(flash);
      toast(f.title, {tone: f.tone, description: f.description});
    }
  } catch (e) {}
})();
