/* Runs in <head>, before the first paint, so the page never flashes the wrong theme
   or shows researcher-only parts for a frame. Kept as a file (not inline) on purpose.

   localStorage keys (unchanged from earlier versions of this panel):
     theme              'light' | 'dark' | absent = follow the system
     advanced           '1' = Technical details on
     sidebar.collapsed  '1' = narrow sidebar */
(function () {
  var root = document.documentElement;
  var systemDark = function () {
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  };
  var theme = null;
  try {
    theme = localStorage.getItem('theme');
    if (localStorage.getItem('advanced') === '1') root.dataset.advanced = '1';
    if (localStorage.getItem('sidebar.collapsed') === '1') root.classList.add('collapsed');
  } catch (e) { /* storage blocked: defaults apply */ }
  root.dataset.theme = theme === 'light' || theme === 'dark' ? theme : (systemDark() ? 'dark' : 'light');
  root.dataset.themeChoice = theme === 'light' || theme === 'dark' ? theme : 'system';
})();
