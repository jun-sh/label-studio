import { useTranslation } from "react-i18next";

/**
 * /collection and /collection?station=* are rendered by Django `collection_viz.html`.
 * React must not mount list/viz layers or rewrite history on this route.
 */
export const CollectionPage = () => {
  useTranslation("common");
  // Django collection_viz.html owns this route; legacy bundles must not mount #datalab-viz-layer.
  if (typeof window !== "undefined" && (window as Window & { __DATALAB_COLLECTION_DJANGO_SHELL__?: boolean }).__DATALAB_COLLECTION_DJANGO_SHELL__) {
    return null;
  }
  return null;
};

CollectionPage.path = "/collection";
CollectionPage.exact = true;
CollectionPage.i18nTitleKey = "collection.page_title";
