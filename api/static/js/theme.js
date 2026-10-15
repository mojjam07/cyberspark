/*
 * Dark / light theme toggle.
 *
 * The initial theme is set by a tiny inline script in <head> (base.html) so the
 * page never flashes the wrong colours. This file wires up everything after
 * that: the toggle button, saving the choice, following the OS setting while
 * the visitor hasn't chosen, and keeping other open tabs in sync.
 */
(function () {
  var KEY = 'cs-theme';
  var META_COLORS = { light: '#f7f7f4', dark: '#0c0c0d' };
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function saved() {
    try {
      var v = localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) { return null; }
  }
  function systemTheme() { return mq && mq.matches ? 'light' : 'dark'; }
  function current() { return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark'; }
  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function apply(theme, animate) {
    if (animate && !reducedMotion()) {
      root.classList.add('theme-switching');
      window.setTimeout(function () { root.classList.remove('theme-switching'); }, 350);
    }
    root.setAttribute('data-theme', theme);

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', META_COLORS[theme]);

    var btn = document.getElementById('themeToggle');
    if (btn) {
      var label = 'Switch to ' + (theme === 'light' ? 'dark' : 'light') + ' mode';
      btn.setAttribute('aria-label', label);
      btn.setAttribute('title', label);
    }
  }

  function toggle() {
    var next = current() === 'light' ? 'dark' : 'light';
    apply(next, true);
    try { localStorage.setItem(KEY, next); } catch (e) { /* private mode: still works for this page view */ }
  }

  // Sync the button label with whatever the head script chose.
  apply(current(), false);

  var btn = document.getElementById('themeToggle');
  if (btn) btn.addEventListener('click', toggle);

  // No saved choice -> keep following the operating-system setting live.
  function onSystemChange() { if (!saved()) apply(systemTheme(), true); }
  if (mq) {
    if (mq.addEventListener) mq.addEventListener('change', onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);
  }

  // Another tab changed the theme.
  window.addEventListener('storage', function (e) {
    if (e.key === KEY && (e.newValue === 'light' || e.newValue === 'dark')) apply(e.newValue, false);
  });
})();
