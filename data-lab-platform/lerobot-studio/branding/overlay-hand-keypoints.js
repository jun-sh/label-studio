/**
 * Legacy non-module script tag — dynamically loads the ES module entry.
 * Do not use `import` here: defer/classic scripts cannot parse import syntax.
 */
(function () {
  "use strict";
  if (globalThis.__DATALAB_HAND_KP2D__) return;
  var src = "/lerobot/branding/overlay-hand-keypoints.mjs?v=21";
  var existing = document.querySelector('script[type="module"][src*="overlay-hand-keypoints.mjs"]');
  if (existing) return;
  var s = document.createElement("script");
  s.type = "module";
  s.src = src;
  document.head.appendChild(s);
})();
