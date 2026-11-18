// Click any table header to sort that column (click again to reverse). Works for tables drawn after the page loads.
(function () {
  const css = document.createElement('style');
  css.textContent = 'th.sortable{cursor:pointer;user-select:none}th.sortable:after{content:" ↕";opacity:.35;font-size:.85em}th.sortable.asc:after{content:" ▲";opacity:.9}th.sortable.desc:after{content:" ▼";opacity:.9}';
  document.head.appendChild(css);
  const num = t => {
    const s = t.replace(/[,\s$%~≈]|pts|km|psf|yr|FY/gi, '').replace(/[−–]/g, '-').replace(/^\+/, '');
    const m = s.match(/^-?\d+(\.\d+)?(k|m|b)?/i);
    if (!m) return null;
    let v = parseFloat(m[0]); const u = (m[2] || '').toLowerCase();
    return v * (u === 'k' ? 1e3 : u === 'm' ? 1e6 : u === 'b' ? 1e9 : 1);
  };
  function enhance(table) {
    if (table.dataset.sortable) return;
    const rows = [...table.rows];
    if (rows.length < 3 || !rows[0].querySelector('th')) return;
    table.dataset.sortable = '1';
    const head = rows[0];
    [...head.cells].forEach((th, i) => {
      th.classList.add('sortable'); th.title = 'Sort';
      th.addEventListener('click', () => {
        const dir = th.classList.contains('asc') ? -1 : 1;
        [...head.cells].forEach(c => c.classList.remove('asc', 'desc'));
        th.classList.add(dir === 1 ? 'asc' : 'desc');
        const body = [...table.rows].slice(1).filter(r => !r.querySelector('th'));
        const key = r => { const c = r.cells[i]; const t = c ? c.textContent.trim() : ''; return {n: num(t), t}; };
        body.sort((a, b) => {
          const x = key(a), y = key(b);
          if (x.n != null && y.n != null) return (x.n - y.n) * dir;
          if (x.n != null) return -1; if (y.n != null) return 1;      // blanks / text-only cells last
          return x.t.localeCompare(y.t) * dir;
        });
        const parent = body[0] && body[0].parentNode;
        body.forEach(r => parent.appendChild(r));
      });
    });
  }
  const scan = () => document.querySelectorAll('table').forEach(enhance);
  new MutationObserver(() => { clearTimeout(scan.t); scan.t = setTimeout(() => { document.querySelectorAll('table[data-sortable]').forEach(t => { if (!t.rows[0] || !t.rows[0].querySelector('th.sortable')) delete t.dataset.sortable; }); scan(); }, 50); })
    .observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('DOMContentLoaded', scan); scan();
})();
