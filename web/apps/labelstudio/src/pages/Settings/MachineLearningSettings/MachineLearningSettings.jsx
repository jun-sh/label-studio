import { useCallback, useContext, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button, Typography, Spinner, EmptyState, SimpleCard } from "@humansignal/ui";
import { useUpdatePageTitle, createTitleFromSegments } from "@humansignal/core";
import { Form, Label, Toggle } from "../../../components/Form";
import { modal } from "../../../components/Modal/Modal";
import { IconModels, IconExternal } from "@humansignal/icons";
import { useAPI } from "../../../providers/ApiProvider";
import { ProjectContext } from "../../../providers/ProjectProvider";
import { MachineLearningList } from "./MachineLearningList";
import { CustomBackendForm } from "./Forms";
import { TestRequest } from "./TestRequest";
import { StartModelTraining } from "./StartModelTraining";
import "./MachineLearningSettings.scss";

export const MachineLearningSettings = () => {
  const { t } = useTranslation("common");
  const api = useAPI();
  const { project, fetchProject } = useContext(ProjectContext);
  const [backends, setBackends] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useUpdatePageTitle(createTitleFromSegments([project?.title, t("settings_ml.page_title_segment")]));

  const fetchBackends = useCallback(async () => {
    setLoading(true);
    const models = await api.callApi("mlBackends", {
      params: {
        project: project.id,
        include_static: true,
      },
    });

    if (models) setBackends(models);
    setLoading(false);
    setLoaded(true);
  }, [project, setBackends]);

  const startTrainingModal = useCallback(
    (backend) => {
      const modalProps = {
        title: t("settings_ml.modal_start_training"),
        style: { width: 760 },
        closeOnClickOutside: true,
        body: <StartModelTraining backend={backend} />,
      };

      modal(modalProps);
    },
    [project, t],
  );

  const showRequestModal = useCallback(
    (backend) => {
      const modalProps = {
        title: t("settings_ml.modal_test_request"),
        style: { width: 760 },
        closeOnClickOutside: true,
        body: <TestRequest backend={backend} />,
      };

      modal(modalProps);
    },
    [project, t],
  );

  const showMLFormModal = useCallback(
    (backend) => {
      const action = backend ? "updateMLBackend" : "addMLBackend";
      const modalProps = {
        title: backend ? t("settings_ml.modal_edit_model") : t("settings_ml.modal_connect_model"),
        style: { width: 760 },
        closeOnClickOutside: false,
        body: (
          <CustomBackendForm
            action={action}
            backend={backend}
            project={project}
            onSubmit={() => {
              fetchBackends();
              modalRef.close();
            }}
          />
        ),
      };

      const modalRef = modal(modalProps);
    },
    [project, fetchBackends, t],
  );

  useEffect(() => {
    if (project.id) {
      fetchBackends();
    }
  }, [project.id]);

  return (
    <section>
      <div className="w-[42rem]">
        <Typography variant="headline" size="medium" className="mb-base">
          {t("breadcrumbs.machine_learning")}
        </Typography>
        {loading && <Spinner size={32} />}
        {loaded && backends.length === 0 && (
          <SimpleCard title="" className="bg-primary-background border-primary-border-subtler p-base">
            <EmptyState
              size="medium"
              variant="primary"
              icon={<IconModels />}
              title={t("settings_ml.empty_title")}
              description={t("settings_ml.empty_description")}
              actions={
                <Button
                  variant="primary"
                  look="filled"
                  onClick={() => showMLFormModal()}
                  aria-label={t("settings_ml.connect_model_aria")}
                >
                  {t("settings_ml.connect_model")}
                </Button>
              }
              footer={
                !window.APP_SETTINGS?.whitelabel_is_active && (
                  <Typography variant="label" size="small" className="text-primary-link">
                    <a
                      href="https://labelstud.io/guide/ml"
                      target="_blank"
                      rel="noopener noreferrer"
                      data-testid="ml-help-link"
                      aria-label={t("settings_ml.learn_more_aria")}
                      className="inline-flex items-center gap-1 hover:underline"
                    >
                      {t("settings_ml.learn_more")}
                      <IconExternal width={16} height={16} />
                    </a>
                  </Typography>
                )
              }
            />
          </SimpleCard>
        )}
        <MachineLearningList
          onEdit={(backend) => showMLFormModal(backend)}
          onTestRequest={(backend) => showRequestModal(backend)}
          onStartTraining={(backend) => startTrainingModal(backend)}
          fetchBackends={fetchBackends}
          backends={backends}
        />

        {backends.length > 0 && (
          <div className="my-wide">
            <Typography size="small" className="text-neutral-content-subtler">
              {t("settings_ml.connected_steps_intro")}
            </Typography>
            <Typography size="small" className="text-neutral-content-subtler mt-base">
              {t("settings_ml.step_1")}
            </Typography>
            <Typography size="small" className="text-neutral-content-subtler mt-tighter">
              {t("settings_ml.step_2")}
            </Typography>
            <Typography size="small" className="text-neutral-content-subtler mt-tighter">
              {t("settings_ml.step_3")}
            </Typography>
            <Typography size="small" className="text-neutral-content-subtler mt-base">
              {t("settings_ml.prelabel_nav_before")}{" "}
              <NavLink to="annotation" className="hover:underline">
                {t("settings_ml.prelabel_nav_link")}
              </NavLink>
              {t("settings_ml.prelabel_nav_after")}
            </Typography>
          </div>
        )}

        <Form
          action="updateProject"
          formData={{ ...project }}
          params={{ pk: project.id }}
          onSubmit={() => fetchProject()}
        >
          {backends.length > 0 && (
            <div className="p-wide border border-neutral-border rounded-md">
              <Form.Row columnCount={1}>
                <Label text={t("settings_ml.configuration")} large />

                <div>
                  <Toggle
                    label={t("settings_ml.toggle_train_label")}
                    description={t("settings_ml.toggle_train_description")}
                    name="start_training_on_annotation_update"
                  />
                </div>
              </Form.Row>
            </div>
          )}

          {backends.length > 0 && (
            <Form.Actions>
              <Form.Indicator>
                <span case="success">{t("settings_ml.saved")}</span>
              </Form.Indicator>
              <Button type="submit" look="primary" className="w-[120px]" aria-label={t("settings_ml.save_aria")}>
                {t("settings_ml.save")}
              </Button>
            </Form.Actions>
          )}
        </Form>
      </div>
    </section>
  );
};

MachineLearningSettings.title = "Model";
MachineLearningSettings.i18nTitleKey = "breadcrumbs.machine_learning";
MachineLearningSettings.path = "/ml";
