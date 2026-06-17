export type DatalabEmbedConfig = {
  layerId: string;
  frameId: string;
  bodyDataset: string;
};

const LAYOUT_EVENT = "datalab:layout";

function hideDuplicateIframes(frameId: string) {
  document.querySelectorAll(".content-wrapper__content iframe").forEach((node) => {
    if (node.id === frameId) return;
    (node as HTMLIFrameElement).style.display = "none";
  });
}

function readSidebarInset() {
  const pinned = localStorage.getItem("sidebar-pinned") === "true";
  const opened = localStorage.getItem("sidebar-opened") === "true";
  const sidebarWidth =
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--menu-sidebar-width")) || 240;

  return pinned && opened ? sidebarWidth : 0;
}

export function syncBounds(config: DatalabEmbedConfig) {
  const layer = document.getElementById(config.layerId);
  if (!layer) return;

  applyLayerChrome(layer);

  const content = document.querySelector(".content-wrapper__content");
  const headerHeight =
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-height")) || 48;
  const insetFromStorage = readSidebarInset();

  if (content) {
    const style = getComputedStyle(content);
    const marginLeft = parseFloat(style.marginLeft) || 0;
    const rect = content.getBoundingClientRect();

    let left: number;
    let width: number;
    if (marginLeft > 0) {
      left = rect.left;
      width = rect.width;
    } else if (insetFromStorage > 0) {
      left = insetFromStorage;
      width = Math.max(0, window.innerWidth - insetFromStorage);
    } else {
      left = rect.left;
      width = rect.width;
    }

    const top = rect.top > 0 ? rect.top : headerHeight;
    const height = Math.max(0, window.innerHeight - top);

    layer.style.top = `${top}px`;
    layer.style.left = `${left}px`;
    layer.style.width = `${width}px`;
    layer.style.height = `${height}px`;
    layer.style.right = "";
    layer.style.bottom = "";
  } else {
    const insetLeft = readSidebarInset();

    layer.style.top = `${headerHeight}px`;
    layer.style.left = `${insetLeft}px`;
    layer.style.width = `${Math.max(0, window.innerWidth - insetLeft)}px`;
    layer.style.height = `${window.innerHeight - headerHeight}px`;
    layer.style.right = "";
    layer.style.bottom = "";
  }

  hideDuplicateIframes(config.frameId);
}

function applyLayerChrome(layer: HTMLElement) {
  layer.style.position = "fixed";
  layer.style.zIndex = "150";
  layer.style.display = "flex";
  layer.style.flexDirection = "column";
  layer.style.overflow = "hidden";
  layer.style.boxSizing = "border-box";
  layer.style.background = "#0b1220";
}

export function mountEmbedLayer(config: DatalabEmbedConfig, src: string, title: string) {
  let layer = document.getElementById(config.layerId);
  if (!layer) {
    layer = document.createElement("div");
    layer.id = config.layerId;
    applyLayerChrome(layer);
    const iframe = document.createElement("iframe");
    iframe.id = config.frameId;
    iframe.src = src;
    iframe.title = title;
    iframe.allow = "fullscreen";
    iframe.style.flex = "1 1 auto";
    iframe.style.width = "100%";
    iframe.style.height = "100%";
    iframe.style.minHeight = "0";
    iframe.style.border = "0";
    iframe.style.display = "block";
    layer.appendChild(iframe);
    document.body.appendChild(layer);
  } else {
    applyLayerChrome(layer);
    const frame = document.getElementById(config.frameId) as HTMLIFrameElement | null;
    if (frame) {
      const resolvedSrc = new URL(src, window.location.origin).href;
      if (frame.src !== resolvedSrc) {
        frame.src = src;
      }
    }
  }
  syncBounds(config);
}

export function attachEmbedLayoutListeners(config: DatalabEmbedConfig): () => void {
  const onResize = () => syncBounds(config);
  const onStorage = (e: StorageEvent) => {
    if (e.key === "sidebar-pinned" || e.key === "sidebar-opened") {
      syncBounds(config);
    }
  };
  const onTransitionEnd = (e: TransitionEvent) => {
    if (e.propertyName === "margin-left") {
      syncBounds(config);
    }
  };
  const onLayoutEvent = () => syncBounds(config);
  const onPinClick = (e: MouseEvent) => {
    if ((e.target as Element | null)?.closest(".sidebar__pin")) {
      requestAnimationFrame(() => syncBounds(config));
      setTimeout(() => syncBounds(config), 160);
    }
  };

  window.addEventListener("resize", onResize);
  window.addEventListener("storage", onStorage);
  window.addEventListener(LAYOUT_EVENT, onLayoutEvent);
  document.addEventListener("click", onPinClick, true);

  const observer = new MutationObserver(() => syncBounds(config));
  const root = document.querySelector(".content-wrapper__body") ?? document.body;

  observer.observe(root, {
    attributes: true,
    childList: true,
    subtree: true,
    attributeFilter: ["class", "style"],
  });

  const sidebar = document.querySelector(".sidebar");
  if (sidebar) {
    observer.observe(sidebar, { attributes: true, attributeFilter: ["class"] });
  }

  const content = document.querySelector(".content-wrapper__content");
  if (content) {
    observer.observe(content, { attributes: true, attributeFilter: ["class", "style"] });
    content.addEventListener("transitionend", onTransitionEnd as EventListener);
  }

  const timers = [0, 50, 160, 300, 500, 800].map((ms) => window.setTimeout(() => syncBounds(config), ms));
  const interval = window.setInterval(() => syncBounds(config), 500);

  return () => {
    window.removeEventListener("resize", onResize);
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(LAYOUT_EVENT, onLayoutEvent);
    document.removeEventListener("click", onPinClick, true);
    observer.disconnect();
    content?.removeEventListener("transitionend", onTransitionEnd as EventListener);
    timers.forEach(clearTimeout);
    clearInterval(interval);
  };
}
