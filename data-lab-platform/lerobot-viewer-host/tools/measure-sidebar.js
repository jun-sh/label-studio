// Paste into the /collection page console (or run via a browser tool) with either engine active.
// Returns rects + text styles of the sidebar landmarks inside the viewer iframe, for old/new diffing.
(() => {
  const d = document.querySelector('iframe').contentDocument;
  const W = d.defaultView;
  const leafWith = (re, root = d.body) => {
    const w = d.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let t;
    while ((t = w.nextNode())) if (re.test(t.textContent)) return t.parentElement;
    return null;
  };
  const up = (el, n) => {
    for (let i = 0; el && i < n; i++) el = el.parentElement;
    return el;
  };
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = W.getComputedStyle(el);
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height), cs.fontSize, cs.fontWeight, cs.color, cs.backgroundColor, cs.fontFamily.slice(0, 18)].join(' ');
  };
  const badge = leafWith(/^# 40$/);
  const row = badge && badge.closest('[role=button]').parentElement;
  return {
    tabEpisodes: box(leafWith(/^任务$/)?.closest('button')),
    tabAnalysis: box(leafWith(/^分析$/)?.closest('button')),
    countTitle: box(leafWith(/^Episodes$/)?.parentElement),
    btnMulti: box(leafWith(/多选/)?.closest('button')),
    btnImport: box(leafWith(/导入/)?.closest('button')),
    search: box(d.querySelector('input[placeholder="搜索..."]')),
    filterBtn: box(d.querySelector('button[title="高级"]')),
    group1: box(leafWith(/^11月4日$/)?.closest('button')),
    group2: box(leafWith(/^11月8日$/)?.closest('button')),
    row40: box(row),
    badge40: box(badge),
    duration40: box(badge && badge.parentElement.querySelector('span')),
    label40: box(row && row.querySelector('span[title*="·"]')),
    row41: box(leafWith(/^# 41$/)?.closest('[role=button]').parentElement),
    sidebar: box(up(leafWith(/^任务$/)?.closest('[role=tablist]'), 3)),
  };
})();
