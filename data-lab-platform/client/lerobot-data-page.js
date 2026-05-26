/**
 * Auth gate + iframe path fix for /lerobot-data (LeRobot Studio embed).
 */
(function () {
  "use strict";

  var DEFAULT_IFRAME =
    "/lerobot/?url=sample%3A%2F%2Fsensexperience_ego";

  function fixIframeNavigation(frame) {
    if (!frame || !frame.contentWindow) return;
    var win;
    try {
      win = frame.contentWindow;
      if (!win.location || !win.location.pathname) return;
    } catch (e) {
      return;
    }

    var path = win.location.pathname;
    var search = win.location.search || "";
    var misroute = path.match(/^\/lerobot-data\/(.+)$/);
    if (misroute) {
      win.location.replace("/lerobot/" + misroute[1] + search);
    }
  }

  var frame = document.getElementById("lerobot-frame");
  if (frame && (!frame.getAttribute("src") || frame.getAttribute("src") === "/lerobot")) {
    frame.setAttribute("src", DEFAULT_IFRAME);
  }
  if (frame) {
    frame.addEventListener("load", function () {
      fixIframeNavigation(frame);
    });
  }

  var loginNext = encodeURIComponent(window.location.pathname + window.location.search);

  fetch("/api/current-user/whoami", { credentials: "same-origin" })
    .then(function (response) {
      if (!response.ok) {
        window.location.replace("/user/login/?next=" + loginNext);
        return null;
      }
      return response.json();
    })
    .then(function (user) {
      if (!user || !user.id) {
        window.location.replace("/user/login/?next=" + loginNext);
      }
    })
    .catch(function () {
      window.location.replace("/user/login/?next=" + loginNext);
    });
})();
