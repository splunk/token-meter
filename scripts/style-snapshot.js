// Computed-style regression harness for page.html. Paste into the browser
// console (or a preview eval) on a running dashboard:
//   await __tmStyle.capture('base')        -> stores a snapshot in localStorage
//   await __tmStyle.diff('base')           -> compares the current page to it
// On a /sessions/<id> page pass {routes: ['summary']} to capture detail tabs.
// Snapshots key elements by a class-path signature, so live data changes add or
// remove signatures but do not register as style changes.
(() => {
  const ROUTES = ['sessions', 'sessions-all', 'sessions-compare', 'sessions-subagents',
    'spend', 'models', 'subagents', 'efficiency', 'git', 'learn', 'capabilities', 'settings'];
  const PROPS = ['color', 'background-color', 'background-image', 'border-top-color',
    'border-right-color', 'border-bottom-color', 'border-left-color', 'border-top-left-radius',
    'border-top-right-radius', 'border-bottom-left-radius', 'border-bottom-right-radius',
    'box-shadow', 'font-size', 'font-weight', 'font-family', 'line-height', 'letter-spacing',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left', 'margin-top',
    'margin-right', 'margin-bottom', 'margin-left', 'row-gap', 'column-gap', 'opacity',
    'outline-color', 'text-transform', 'fill', 'stroke'];
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const part = el => {
    const cls = typeof el.className === 'string' ? el.className : el.getAttribute('class') || '';
    const c = cls.split(/\s+/).filter(Boolean).map(x => x.replace(/\d+/g, '#')).sort().join('.');
    return el.tagName.toLowerCase() + (c ? '.' + c : '');
  };
  const signature = el => {
    const out = [];
    for (let n = el, i = 0; n && n.nodeType === 1 && i < 5; n = n.parentElement, i++) out.unshift(part(n));
    return out.join('>');
  };
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  async function captureRoute(route) {
    location.hash = route;
    await wait(1600);
    const styles = {}, overflow = [];
    for (const el of document.body.querySelectorAll('*')) {
      if (!visible(el) || el.closest('script,style')) continue;
      const cs = getComputedStyle(el), sig = signature(el);
      const key = PROPS.map(p => cs.getPropertyValue(p)).join('|');
      (styles[sig] ||= {})[key] = 1;
      if (el.scrollWidth > el.clientWidth + 1 && cs.overflowX === 'visible' && el.clientWidth > 0
        && !['svg', 'path', 'g', 'text'].includes(el.tagName.toLowerCase())) overflow.push(sig);
    }
    const page = document.documentElement.scrollWidth > window.innerWidth + 1;
    return { styles, overflow: [...new Set(overflow)], pageOverflow: page };
  }
  async function snapshot(routes) {
    const out = { width: window.innerWidth, routes: {} };
    for (const r of routes) out.routes[r] = await captureRoute(r);
    return out;
  }
  async function capture(name, { routes = ROUTES } = {}) {
    const snap = await snapshot(routes);
    localStorage.setItem('tmStyle:' + name + ':' + snap.width, JSON.stringify(snap));
    return { stored: name, width: snap.width, routes: Object.keys(snap.routes).length };
  }
  async function diff(name, { limit = 60 } = {}) {
    const stored = localStorage.getItem('tmStyle:' + name + ':' + window.innerWidth);
    const width = window.innerWidth;
    const base = JSON.parse(stored || 'null');
    if (!base) return { error: 'no baseline ' + name + ' at width ' + width };
    const routes = Object.keys(base.routes);
    const now = await snapshot(routes), changes = {}, report = { width, routes: {} };
    for (const r of routes) {
      const a = base.routes[r], b = now.routes[r];
      let changed = 0;
      for (const sig of Object.keys(b.styles)) {
        if (!a.styles[sig]) continue;
        const ak = Object.keys(a.styles[sig]).sort().join('\n'), bk = Object.keys(b.styles[sig]).sort().join('\n');
        if (ak === bk) continue;
        changed++;
        const av = Object.keys(a.styles[sig])[0].split('|'), bv = Object.keys(b.styles[sig])[0].split('|');
        PROPS.forEach((p, i) => {
          if (av[i] !== bv[i]) {
            const k = p + ': ' + av[i].slice(0, 80) + ' -> ' + bv[i].slice(0, 80);
            changes[k] = (changes[k] || 0) + 1;
          }
        });
      }
      const newOverflow = b.overflow.filter(s => !a.overflow.includes(s));
      report.routes[r] = { changed, newOverflow: newOverflow.slice(0, 8), pageOverflow: b.pageOverflow };
    }
    report.changes = Object.entries(changes).sort((x, y) => y[1] - x[1]).slice(0, limit);
    return report;
  }
  window.__tmStyle = { capture, diff, ROUTES };
})();
