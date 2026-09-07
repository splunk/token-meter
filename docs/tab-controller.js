(function exposeTabController(root, factory) {
  'use strict';

  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TokenMeterTabs = api;
}(typeof window === 'undefined' ? null : window, () => {
  'use strict';

  const nextTabIndex = (key, index, count) => {
    if (!count) return null;
    if (key === 'ArrowRight') return (index + 1) % count;
    if (key === 'ArrowLeft') return (index - 1 + count) % count;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    return null;
  };

  const selectTab = (tabs, selectedTab, findPanel) => {
    tabs.forEach(tab => {
      const selected = tab === selectedTab;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      const panel = findPanel(tab.dataset.tabTarget);
      if (panel) panel.hidden = !selected;
    });
  };

  const setupTabList = (tabList, documentRoot) => {
    const tabs = [...tabList.querySelectorAll('[role="tab"]')];
    const choose = tab => selectTab(tabs, tab, id => documentRoot.getElementById(id));

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => choose(tab));
      tab.addEventListener('keydown', event => {
        const nextIndex = nextTabIndex(event.key, index, tabs.length);
        if (nextIndex === null) return;
        event.preventDefault();
        choose(tabs[nextIndex]);
        tabs[nextIndex].focus();
      });
    });
  };

  const setupAll = documentRoot => {
    documentRoot.querySelectorAll('[data-tab-group]')
      .forEach(tabList => setupTabList(tabList, documentRoot));
  };

  return { nextTabIndex, selectTab, setupTabList, setupAll };
}));
