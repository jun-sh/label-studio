import { Badge, Button, Select, Typography, Tooltip, EnterpriseBadge } from "@humansignal/ui";
import { useCallback, useContext, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { IconSpark } from "@humansignal/icons";
import { Form, Input, TextArea } from "../../components/Form";
import { RadioGroup } from "../../components/Form/Elements/RadioGroup/RadioGroup";
import { ProjectContext } from "../../providers/ProjectProvider";
import { cn } from "../../utils/bem";
import { HeidiTips } from "../../components/HeidiTips/HeidiTips";
import { FF_LSDV_E_297, isFF } from "../../utils/feature-flags";
import { createURL } from "../../components/HeidiTips/utils";

export const GeneralSettings = () => {
  const { t } = useTranslation("common");
  const { project, fetchProject } = useContext(ProjectContext);

  const updateProject = useCallback(() => {
    if (project.id) fetchProject(project.id, true);
  }, [project]);

  const colors = ["#FDFDFC", "#FF4C25", "#FF750F", "#ECB800", "#9AC422", "#34988D", "#617ADA", "#CC6FBE"];

  const samplings = useMemo(
    () => [
      {
        value: "Sequential sampling",
        label: t("project_settings.general.sequential_sampling"),
        description: t("project_settings.general.sequential_desc"),
      },
      {
        value: "Uniform sampling",
        label: t("project_settings.general.random_sampling"),
        description: t("project_settings.general.random_desc"),
      },
    ],
    [t],
  );

  return (
    <div className={cn("general-settings").toClassName()}>
      <div className={cn("general-settings").elem("wrapper").toClassName()}>
        <h1>{t("project_settings.general.page_title")}</h1>
        <div className={cn("settings-wrapper").toClassName()}>
          <Form action="updateProject" formData={{ ...project }} params={{ pk: project.id }} onSubmit={updateProject}>
            <Form.Row columnCount={1} rowGap="16px">
              <Input name="title" label={t("project_settings.general.project_name")} />

              <TextArea name="description" label={t("project_settings.general.description")} style={{ minHeight: 128 }} />
              {isFF(FF_LSDV_E_297) && (
                <div className={cn("workspace-placeholder").toClassName()}>
                  <div className={cn("workspace-placeholder").elem("badge-wrapper").toClassName()}>
                    <div className={cn("workspace-placeholder").elem("title").toClassName()}>
                      {t("project_settings.general.workspace")}
                    </div>
                    <EnterpriseBadge size="small" className="ml-2" />
                  </div>
                  <Select placeholder={t("project_settings.general.select_option")} disabled options={[]} />
                  <Typography size="small" className="my-tight">
                    {t("project_settings.general.workspace_hint")}{" "}
                    <a
                      target="_blank"
                      href={createURL(
                        "https://docs.humansignal.com/guide/manage_projects#Create-workspaces-to-organize-projects",
                        {
                          experiment: "project_settings_tip",
                          treatment: "simplify_project_management",
                        },
                      )}
                      rel="noreferrer"
                      className="underline hover:no-underline"
                    >
                      {t("project_settings.general.learn_more")}
                    </a>
                  </Typography>
                </div>
              )}
              <RadioGroup name="color" label={t("project_settings.general.color")} size="large" labelProps={{ size: "large" }}>
                {colors.map((color) => (
                  <RadioGroup.Button key={color} value={color}>
                    <div className={cn("color").toClassName()} style={{ "--background": color }} />
                  </RadioGroup.Button>
                ))}
              </RadioGroup>

              <RadioGroup label={t("project_settings.general.task_sampling")} labelProps={{ size: "large" }} name="sampling" simple>
                {samplings.map(({ value, label, description }) => (
                  <RadioGroup.Button key={value} value={value} label={label} description={description} />
                ))}
                {isFF(FF_LSDV_E_297) && (
                  <RadioGroup.Button
                    key="uncertainty-sampling"
                    value=""
                    label={
                      <>
                        {t("project_settings.general.uncertainty_label")}{" "}
                        <Tooltip title={t("project_settings.general.uncertainty_tooltip")}>
                          <Badge
                            variant="enterprise"
                            icon={<IconSpark />}
                            size="small"
                            style="ghost"
                            className="ml-tightest"
                          />
                        </Tooltip>
                      </>
                    }
                    disabled
                    description={
                      <>
                        {t("project_settings.general.uncertainty_desc")}{" "}
                        <a
                          target="_blank"
                          href={createURL("https://docs.humansignal.com/guide/active_learning", {
                            experiment: "project_settings_workspace",
                            treatment: "workspaces",
                          })}
                          rel="noreferrer"
                        >
                          {t("project_settings.general.learn_more")}
                        </a>
                      </>
                    }
                  />
                )}
              </RadioGroup>
            </Form.Row>

            <Form.Actions>
              <Form.Indicator>
                <span case="success">{t("project_settings.general.saved")}</span>
              </Form.Indicator>
              <Button type="submit" className="w-[150px]" aria-label={t("project_settings.general.save_aria")}>
                {t("project_settings.general.save")}
              </Button>
            </Form.Actions>
          </Form>
        </div>
      </div>
      {isFF(FF_LSDV_E_297) && <HeidiTips collection="projectSettings" />}
    </div>
  );
};

GeneralSettings.title = "General";
GeneralSettings.i18nTitleKey = "breadcrumbs.general";
GeneralSettings.path = "/";
GeneralSettings.exact = true;
