import { useCallback, useContext, useEffect, useState } from "react";
import { Divider } from "../../../components/Divider/Divider";
import { EmptyState, SimpleCard } from "@humansignal/ui";
import { IconPredictions, Typography, IconExternal } from "@humansignal/ui";
import { useUpdatePageTitle, createTitleFromSegments } from "@humansignal/core";
import { useTranslation } from "react-i18next";
import { useAPI } from "../../../providers/ApiProvider";
import { ProjectContext } from "../../../providers/ProjectProvider";
import { Spinner } from "../../../components/Spinner/Spinner";
import { PredictionsList } from "./PredictionsList";

export const PredictionsSettings = () => {
  const { t } = useTranslation("common");
  const api = useAPI();
  const { project } = useContext(ProjectContext);
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useUpdatePageTitle(createTitleFromSegments([project?.title, t("settings_predictions.page_title_segment")]));

  const fetchVersions = useCallback(async () => {
    setLoading(true);
    const versions = await api.callApi("projectModelVersions", {
      params: {
        pk: project.id,
        extended: true,
      },
    });

    if (versions) setVersions(versions.static);
    setLoading(false);
    setLoaded(true);
  }, [project, setVersions]);

  useEffect(() => {
    if (project.id) {
      fetchVersions();
    }
  }, [project]);

  return (
    <section className="max-w-[42rem]">
      <Typography variant="headline" size="medium" className="mb-tight">
        {t("breadcrumbs.predictions")}
      </Typography>
      <div>
        {loading && <Spinner size={32} />}

        {loaded && versions.length > 0 && (
          <>
            <Typography variant="title" size="medium">
              {t("settings_predictions.list_heading")}
            </Typography>
            <Typography size="small" className="text-neutral-content-subtler mt-base mb-wider">
              {t("settings_predictions.list_description_before")}{" "}
              <a href="https://labelstud.io/guide/predictions.html" target="_blank" rel="noreferrer">
                {t("settings_predictions.list_description_link")}
              </a>
              {t("settings_predictions.list_description_after")}
            </Typography>
          </>
        )}

        {loaded && versions.length === 0 && (
          <SimpleCard title="" className="bg-primary-background border-primary-border-subtler p-base">
            <EmptyState
              size="medium"
              variant="primary"
              icon={<IconPredictions />}
              title={t("settings_predictions.empty_title")}
              description={t("settings_predictions.empty_description")}
              footer={
                !window.APP_SETTINGS?.whitelabel_is_active && (
                  <Typography variant="label" size="small" className="text-primary-link">
                    <a
                      href="https://labelstud.io/guide/predictions"
                      target="_blank"
                      rel="noopener noreferrer"
                      data-testid="predictions-help-link"
                      aria-label={t("settings_predictions.learn_more_aria")}
                      className="inline-flex items-center gap-1 hover:underline"
                    >
                      {t("settings_predictions.learn_more")}
                      <IconExternal width={16} height={16} />
                    </a>
                  </Typography>
                )
              }
            />
          </SimpleCard>
        )}

        <PredictionsList project={project} versions={versions} fetchVersions={fetchVersions} />

        <Divider height={32} />
      </div>
    </section>
  );
};

PredictionsSettings.title = "Predictions";
PredictionsSettings.i18nTitleKey = "breadcrumbs.predictions";
PredictionsSettings.path = "/predictions";
