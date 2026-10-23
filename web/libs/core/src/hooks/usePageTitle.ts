import { useEffect } from "react";

export const PAGE_TITLE_SEPARATOR = " | ";

export const getBasePageTitle = () => {
  return ((window.APP_SETTINGS as Record<string, unknown>)?.page_title as string)?.split(PAGE_TITLE_SEPARATOR).at(-1);
};

export const getPageTitle = (pageTitle?: string) => {
  const baseTitle = getBasePageTitle();
  return pageTitle && baseTitle ? `${pageTitle}${PAGE_TITLE_SEPARATOR}${baseTitle}` : pageTitle || baseTitle;
};

export const createTitleFromSegments = (titleSegments: (string | undefined)[]) => {
  return titleSegments.filter(Boolean).join(PAGE_TITLE_SEPARATOR);
};

export const setPageTitle = (_pageTitle?: string, _includeBaseTitle = true) => {
  const title = getBasePageTitle() || "Datalab";
  if (typeof document !== "undefined") {
    document.title = title;
  }
};

export const useUpdatePageTitle = (title?: string, includeBaseTitle = true) => {
  useEffect(() => {
    if (!title) return;
    // Debounce title update to avoid flickering when navigating between pages
    const requestId = requestAnimationFrame(() => {
      setPageTitle(title, includeBaseTitle);
    });
    return () => {
      if (requestId) cancelAnimationFrame(requestId);
    };
  }, [title, includeBaseTitle]);
};
