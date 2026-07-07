/**
 * /data embed only (injected when ?datalab_embed=1).
 * Rewrites upstream sample manifest fetches to same-origin /lerobot/sample-datasets.manifest.json.
 * Does not patch minified bundles; does not intercept non-manifest requests.
 */
(function manifestEmbedFix() {
  "use strict";

  var LOG = "[datalab:manifest-embed-fix]";
  var MANIFEST_PATH = "/lerobot/sample-datasets.manifest.json";

  function manifestTargetUrl() {
    return new URL(MANIFEST_PATH, window.location.origin).href;
  }

  /**
   * Whitelist: only URLs that look like the upstream sample manifest loader.
   * Matches COS manifest, root /sample-datasets.manifest.json, and /lerobot/... variants.
   */
  function isManifestRequest(input) {
    var raw =
      typeof input === "string"
        ? input
        : input && typeof input.url === "string"
          ? input.url
          : "";
    if (!raw) return false;

    try {
      var u = new URL(raw, window.location.origin);
      var path = u.pathname || "";
      if (/\/sample-datasets\.manifest\.json$/i.test(path)) return true;
      if (u.hostname.indexOf("myqcloud.com") >= 0 && path.indexOf("manifest") >= 0) return true;
      return false;
    } catch (err) {
      console.error(LOG, "failed to parse fetch URL:", raw, err);
      return false;
    }
  }

  var nativeFetch = window.fetch.bind(window);

  window.fetch = function datalabManifestFetch(input, init) {
    if (!isManifestRequest(input)) {
      return nativeFetch(input, init);
    }

    var target = manifestTargetUrl();
    console.info(LOG, "rewrite manifest fetch ->", target);

    return nativeFetch(target, init)
      .then(function (response) {
        if (!response.ok) {
          console.error(LOG, "manifest HTTP error:", response.status, target);
        }
        return response;
      })
      .then(function (response) {
        if (!response.ok) return response;
        return response
          .clone()
          .json()
          .then(function (body) {
            if (!body || body.schemaVersion !== 1 || !Array.isArray(body.datasets)) {
              console.error(LOG, "invalid manifest schema from", target, body);
            } else if (!body.datasets.some(function (d) { return d && d.id === "sensexperience_ego"; })) {
              console.error(LOG, "manifest missing sensexperience_ego:", target, body.datasets.map(function (d) { return d && d.id; }));
            }
          })
          .catch(function (err) {
            console.error(LOG, "manifest JSON parse error:", target, err);
          })
          .then(function () {
            return response;
          });
      })
      .catch(function (err) {
        console.error(LOG, "manifest fetch failed:", target, err);
        throw err;
      });
  };
})();
