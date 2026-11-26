// Computed-style regression harness for the dashboard page. Paste into the browser
// console (or a preview eval) on a running dashboard:
//   await __tmStyle.capture('base')        -> stores a snapshot in localStorage
//   await __tmStyle.diff('base')           -> compares the current page to it
// On a /sessions/<id> page pass {routes: ['summary']} to capture detail tabs.
// Pass {theme: 'frontier'} to capture and diff one theme without changing the saved preference.
// When a change adds a shared class, pass diff('base', {ignoreClasses: ['metric']}) so the
// migrated elements are compared with their old selves instead of reported as new.
// Snapshots key elements by a class-path signature. Signatures present on only
// one side (live data or renamed classes) are counted separately from style changes.
(() => {
  const ROUTES = ['sessions', 'sessions-all', 'sessions-compare', 'sessions-subagents',
    'spend', 'models', 'subagents', 'efficiency', 'git', 'learn', 'capabilities', 'settings'];
  const PROPS = ['color', 'background-color', 'background-image', 'border-top-color',
    'border-right-color', 'border-bottom-color', 'border-left-color', 'border-top-left-radius',
    'border-top-right-radius', 'border-bottom-left-radius', 'border-bottom-right-radius',
    'box-shadow', 'font-size', 'font-weight', 'font-family', 'line-height', 'letter-spacing',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left', 'margin-top',
    'margin-right', 'margin-bottom', 'margin-left', 'row-gap', 'column-gap', 'opacity',
    'outline-color', 'text-transform', 'fill', 'stroke',
    // Theme finishes: dot fills and tracks (mask, min-width), glass, and title paint.
    'mask-image', 'mask-position', 'mask-size', 'min-width', 'background-position', 'background-size',
    'backdrop-filter', 'font-stretch'];
  const wait = ms => new Promise(r => setTimeout(r, ms));
  let ignored = new Set();
  const part = el => {
    const cls = typeof el.className === 'string' ? el.className : el.getAttribute('class') || '';
    const c = cls.split(/\s+/).filter(name => name && !ignored.has(name)).map(x => x.replace(/\d+/g, '#')).sort().join('.');
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
    const styles = {}, overflow = [], clipped = [];
    const SKIP = ['svg', 'path', 'g', 'text', 'line', 'rect', 'circle', 'input', 'select', 'textarea'];
    for (const el of document.body.querySelectorAll('*')) {
      if (!visible(el) || el.closest('script,style')) continue;
      const cs = getComputedStyle(el), sig = signature(el);
      const key = PROPS.map(p => cs.getPropertyValue(p)).join('|');
      (styles[sig] ||= {})[key] = 1;
      if (SKIP.includes(el.tagName.toLowerCase()) || el.clientWidth === 0) continue;
      const scrolls = v => v === 'auto' || v === 'scroll';
      const wide = el.scrollWidth > el.clientWidth + 1, tall = el.scrollHeight > el.clientHeight + 1;
      if (wide && cs.overflowX === 'visible') overflow.push(sig);
      // Text cut off by overflow:hidden/clip or ellipsis, horizontally or vertically.
      const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      if (hasText && ((wide && !scrolls(cs.overflowX) && cs.overflowX !== 'visible')
        || (tall && !scrolls(cs.overflowY) && cs.overflowY !== 'visible'))) clipped.push(sig);
    }
    const page = document.documentElement.scrollWidth > window.innerWidth + 1;
    return { styles, overflow: [...new Set(overflow)], clipped: [...new Set(clipped)], pageOverflow: page };
  }
  // Switch to a theme for this tab only (the stored preference is untouched). It skips setTheme's
  // View Transition, which never finishes in a tab that is not painting, and repaints the charts.
  async function useTheme(theme) {
    if (!theme || document.documentElement.dataset.theme === theme) return;
    if (!window.tokenMeterTheme?.ids?.includes(theme)) throw new Error('unknown theme ' + theme);
    document.documentElement.dataset.theme = theme;
    if (typeof window.repaintThemedSurfaces === 'function') window.repaintThemedSurfaces();
    await wait(900);
  }
  const storageKey = (name, theme, width) => 'tmStyle:' + name + ':' + (theme ? theme + ':' : '') + width;
  // Read styles with transitions and animations off: a background tab never advances them, so a route
  // or theme change would otherwise report the previous value.
  async function snapshot(routes, theme) {
    const settled = document.createElement('style');
    settled.textContent = '*,*::before,*::after{transition:none!important;animation:none!important}';
    document.head.append(settled);
    try {
      await useTheme(theme);
      const out = { width: window.innerWidth, theme: document.documentElement.dataset.theme, routes: {} };
      for (const r of routes) out.routes[r] = await captureRoute(r);
      return out;
    } finally {
      settled.remove();
    }
  }
  // Pass {theme: 'frontier'} to capture or diff one theme; the snapshot is stored per theme.
  async function capture(name, { routes = ROUTES, theme = '' } = {}) {
    const snap = await snapshot(routes, theme);
    const key = storageKey(name, theme, snap.width), value = JSON.stringify(snap);
    try {
      localStorage.setItem(key, value);
    } catch (error) {
      // Storage is full of older snapshots: keep only this one and retry.
      Object.keys(localStorage).filter(k => k.startsWith('tmStyle:') && k !== key).forEach(k => localStorage.removeItem(k));
      localStorage.setItem(key, value);
    }
    return { stored: name, width: snap.width, routes: Object.keys(snap.routes).length };
  }
  async function diff(name, { limit = 60, ignoreClasses = [], theme = '' } = {}) {
    ignored = new Set(ignoreClasses);
    const stored = localStorage.getItem(storageKey(name, theme, window.innerWidth));
    const width = window.innerWidth;
    const base = JSON.parse(stored || 'null');
    if (!base) return { error: 'no baseline ' + name + (theme ? ' in ' + theme : '') + ' at width ' + width };
    const routes = Object.keys(base.routes);
    const now = await snapshot(routes, theme), changes = {}, report = { width, theme: now.theme, routes: {} };
    for (const r of routes) {
      const a = base.routes[r], b = now.routes[r];
      let changed = 0;
      const onlyBase = Object.keys(a.styles).filter(sig => !b.styles[sig]);
      const onlyNow = Object.keys(b.styles).filter(sig => !a.styles[sig]);
      for (const sig of Object.keys(b.styles)) {
        if (!a.styles[sig]) continue;
        // Compare variant sets: pair each removed variant with an added one in sorted order.
        const removed = Object.keys(a.styles[sig]).filter(k => !b.styles[sig][k]).sort();
        const added = Object.keys(b.styles[sig]).filter(k => !a.styles[sig][k]).sort();
        if (!removed.length && !added.length) continue;
        changed++;
        for (let i = 0; i < Math.max(removed.length, added.length); i++) {
          const av = (removed[i] || removed[0] || '').split('|'), bv = (added[i] || added[0] || '').split('|');
          PROPS.forEach((p, j) => {
            if (av[j] !== bv[j]) {
              const k = sig.split('>').slice(-2).join('>') + ' :: ' + p + ': ' + String(av[j]).slice(0, 70) + ' -> ' + String(bv[j]).slice(0, 70);
              changes[k] = (changes[k] || 0) + 1;
            }
          });
        }
      }
      const newOverflow = b.overflow.filter(s => !a.overflow.includes(s));
      const newClipped = (b.clipped || []).filter(s => !(a.clipped || []).includes(s));
      report.routes[r] = { changed, newOverflow: newOverflow.slice(0, 8), newClipped: newClipped.slice(0, 8),
        pageOverflow: b.pageOverflow, onlyBase: onlyBase.length, onlyNow: onlyNow.length,
        onlySample: onlyBase.slice(0, 3).concat(onlyNow.slice(0, 3)) };
    }
    report.changes = Object.entries(changes).sort((x, y) => y[1] - x[1]).slice(0, limit);
    return report;
  }
  window.__tmStyle = { capture, diff, ROUTES };
})();
