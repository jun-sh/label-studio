import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@humansignal/ui";
import { useUpdatePageTitle, createTitleFromSegments } from "@humansignal/core";
import { Form, TextArea, Toggle } from "../../components/Form";
import { MenubarContext } from "../../components/Menubar/Menubar";
import { cn } from "../../utils/bem";

import { ModelVersionSelector } from "./AnnotationSettings/ModelVersionSelector";
import { ProjectContext } from "../../providers/ProjectProvider";
import { Divider } from "../../components/Divider/Divider";

export const AnnotationSettings = () => {
  const { t } = useTranslation("common");
  const { project, fetchProject } = useContext(ProjectContext);
  const pageContext = useContext(MenubarContext);
  const formRef = useRef();
  const [collab, setCollab] = useState(null);

  useUpdatePageTitle(createTitleFromSegments([project?.title, t("annotation_settings.page_title_segment")]));

  useEffect(() => {
    pageContext.setProps({ formRef });
  }, [formRef]);

  const updateProject = useCallback(() => {
    fetchProject(project.id, true);
  }, [project]);

  return (
    <div className={cn("annotation-settings").toClassName()}>
      <div className={cn("annotation-settings").elem("wrapper").toClassName()}>
        <h1>{t("annotation_settings.page_heading")}</h1>
        <div className={cn("settings-wrapper").toClassName()}>
          <Form
            ref={formRef}
            action="updateProject"
            formData={{ ...project }}
            params={{ pk: project.id }}
            onSubmit={updateProject}
          >
            <Form.Row columnCount={1}>
              <div className={cn("settings-wrapper").elem("header").toClassName()}>
                {t("annotation_settings.instructions_header")}
              </div>
              <div className="settings-description">
                <p style={{ marginBottom: "0" }}>{t("annotation_settings.instructions_para1")}</p>
                <p style={{ marginTop: "8px" }}>{t("annotation_settings.instructions_para2")}</p>
              </div>
              <div>
                <Toggle label={t("annotation_settings.show_before_labeling")} name="show_instruction" />
              </div>
              <TextArea name="expert_instruction" style={{ minHeight: 128, maxWidth: "520px" }} />
            </Form.Row>

            <Divider height={32} />

            <Form.Row columnCount={1}>
              <br />
              <div className={cn("settings-wrapper").elem("header").toClassName()}>
                {t("annotation_settings.prelabeling_header")}
              </div>
              <div>
                <Toggle
                  label={t("annotation_settings.prelabeling_toggle")}
                  description={<span>{t("annotation_settings.prelabeling_toggle_desc")}</span>}
                  name="show_collab_predictions"
                  onChange={(e) => {
                    setCollab(e.target.checked);
                  }}
                />
              </div>

              {(collab !== null ? collab : project.show_collab_predictions) && <ModelVersionSelector />}
            </Form.Row>

            <Form.Actions>
              <Form.Indicator>
                <span case="success">{t("annotation_settings.saved")}</span>
              </Form.Indicator>
              <Button type="submit" look="primary" className="w-[150px]" aria-label={t("annotation_settings.save_aria")}>
                {t("annotation_settings.save")}
              </Button>
            </Form.Actions>
          </Form>
        </div>
      </div>
    </div>
  );
};

AnnotationSettings.title = "Annotation";
AnnotationSettings.i18nTitleKey = "breadcrumbs.annotation";
AnnotationSettings.path = "/annotation";
