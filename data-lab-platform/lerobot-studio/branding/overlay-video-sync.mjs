/**
 * Sync overlay updates to decoded video frames (not display refresh rate).
 */

export function frameIndexFromVideoTime(video, fps = 30) {
  const rate = Number(fps) || 30;
  const t = Number(video?.currentTime) || 0;
  return Math.max(0, Math.floor(t * rate + 0.5));
}

export function bindVideoFrameUpdates(video, onFrame, options = {}) {
  if (!video || typeof onFrame !== "function") return () => {};
  const fps = Number(options.fps) || 30;
  let lastIdx = -1;
  let rvfHandle = 0;
  let stopped = false;
  const useRvf = typeof video.requestVideoFrameCallback === "function";

  const emitIfChanged = () => {
    if (stopped || !video.isConnected) return;
    const idx = frameIndexFromVideoTime(video, fps);
    if (idx === lastIdx) return;
    lastIdx = idx;
    onFrame(idx);
  };

  const schedule = () => {
    if (stopped || !video.isConnected) return;
    if (useRvf) {
      rvfHandle = video.requestVideoFrameCallback(() => {
        emitIfChanged();
        schedule();
      });
      return;
    }
    emitIfChanged();
  };

  const onTimeUpdate = () => {
    if (useRvf) return;
    emitIfChanged();
  };
  const onSeeked = () => {
    lastIdx = -1;
    emitIfChanged();
  };

  video.addEventListener("timeupdate", onTimeUpdate);
  video.addEventListener("seeked", onSeeked);
  schedule();

  return () => {
    stopped = true;
    video.removeEventListener("timeupdate", onTimeUpdate);
    video.removeEventListener("seeked", onSeeked);
    if (rvfHandle && typeof video.cancelVideoFrameCallback === "function") {
      try {
        video.cancelVideoFrameCallback(rvfHandle);
      } catch {
        /* ignore */
      }
    }
  };
}
