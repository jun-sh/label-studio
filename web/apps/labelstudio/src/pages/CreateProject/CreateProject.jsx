import { Select, ToastContext, ToastType } from "@humansignal/ui";
import React from "react";
import { useTranslation } from "react-i18next";
import { useHistory } from "react-router";
import { Button } from "@humansignal/ui";
import { Modal } from "../../components/Modal/Modal";
import { Space } from "../../components/Space/Space";
import { useAPI } from "../../providers/ApiProvider";
import { cn } from "../../utils/bem";
import "./CreateProject.scss";
import { useDraftProject } from "./utils/useDraftProject";
import { Input, TextArea } from "../../components/Form";
import { buildEmbodiedProjectDescription } from "../EmbodiedAnnotate/embodiedAnnotate";

export const PROJECT_TYPE = {
  multimodal: "multimodal",
  embodied: "embodied",
};

const ProjectName = ({
  name,
  setName,
  onSaveName,
  onSubmit,
  error,
  description,
  setDescription,
  projectType,
  setProjectType,
  projectTypeOptions,
}) => {
  const { t } = useTranslation("common");
  return (
    <form
      className={cn("project-name").toClassName()}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="w-full flex flex-col gap-2">
        <label className="w-full" htmlFor="project_name">
          {t("projects.create_modal.project_name_label")}
        </label>
        <Input
          name="name"
          id="project_name"
          value={name ?? ""}
          onChange={(e) => setName(e.target.value)}
          onBlur={onSaveName}
          className="project-title w-full"
        />
        {error && <span className="-mt-1 text-negative-content">{error}</span>}
      </div>
      <div className="w-full flex flex-col gap-2">
        <label className="w-full" htmlFor="project_description">
          {t("projects.create_modal.description_label")}
        </label>
        <TextArea
          name="description"
          id="project_description"
          placeholder={t("projects.create_modal.description_placeholder")}
          rows="4"
          style={{ minHeight: 100 }}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="project-description w-full"
        />
      </div>
      <div className="w-full flex flex-col gap-2">
        <label className="w-full" htmlFor="project_type">
          {t("projects.create_modal.project_type_label")}
        </label>
        <Select
          id="project_type"
          value={projectType}
          options={projectTypeOptions}
          onChange={(val) => setProjectType(val ?? PROJECT_TYPE.multimodal)}
          triggerClassName="!flex-1"
        />
      </div>
    </form>
  );
};

export const CreateProject = ({ onClose }) => {
  const { t } = useTranslation("common");
  const [waiting, setWaitingStatus] = React.useState(false);

  const { project, setProject: updateProject } = useDraftProject();
  const history = useHistory();
  const api = useAPI();
  const toast = React.useContext(ToastContext);

  const [name, setName] = React.useState("");
  const [error, setError] = React.useState();
  const [description, setDescription] = React.useState("");
  const [projectType, setProjectType] = React.useState(PROJECT_TYPE.multimodal);

  const projectTypeOptions = React.useMemo(
    () => [
      { value: PROJECT_TYPE.multimodal, label: t("projects.create_modal.project_type_multimodal") },
      { value: PROJECT_TYPE.embodied, label: t("projects.create_modal.project_type_embodied") },
    ],
    [t],
  );

  React.useEffect(() => {
    setError(null);
  }, [name]);

  // name intentionally skipped from deps:
  // this should trigger only once when we got project loaded
  React.useEffect(() => {
    if (project?.title && !name) setName(project.title);
  }, [project]);

  const onCreate = React.useCallback(async () => {
    const isEmbodied = projectType === PROJECT_TYPE.embodied;
    const finalDescription = isEmbodied ? buildEmbodiedProjectDescription(description) : description;

    const response = await api.callApi("updateProject", {
      params: {
        pk: project.id,
      },
      body: {
        title: name,
        description: finalDescription,
        label_config: project?.label_config ?? "<View></View>",
        is_draft: false,
      },
    });

    if (response === null) return;

    setWaitingStatus(true);

    __lsa("create_project.create", { project_type: projectType });

    setWaitingStatus(false);

    if (isEmbodied) {
      history.push(`/projects/${response.id}/embodied`);
    } else {
      toast.show({
        message: t("projects.create_modal.multimodal_created_toast"),
        type: ToastType.info,
      });
      history.push(`/projects/${response.id}/data`);
    }
  }, [project, name, description, projectType, api, history, toast, t]);

  const onSaveName = async () => {
    if (error) return;
    const res = await api.callApi("updateProjectRaw", {
      params: {
        pk: project.id,
      },
      body: {
        title: name,
      },
    });

    if (res.ok) return;
    const err = await res.json();

    setError(err.validation_errors?.title);
  };

  const onDelete = React.useCallback(() => {
    const performClose = async () => {
      setWaitingStatus(true);
      if (project)
        await api.callApi("deleteProject", {
          params: {
            pk: project.id,
          },
        });
      setWaitingStatus(false);
      updateProject(null);
      onClose?.();
    };
    performClose();
  }, [project]);

  const rootClass = cn("create-project");

  return (
    <Modal onHide={onDelete} closeOnClickOutside={false} allowToInterceptEscape fullscreen visible bare>
      <div className={rootClass}>
        <Modal.Header>
          <h1>{t("projects.create_modal.title")}</h1>

          <Space>
            <Button
              variant="negative"
              look="outlined"
              onClick={onDelete}
              waiting={waiting}
              aria-label={t("projects.create_modal.cancel_aria")}
            >
              {t("projects.create_modal.cancel")}
            </Button>
            <Button
              look="primary"
              onClick={onCreate}
              waiting={waiting}
              waitingClickable={false}
              disabled={!project || !!error || !(name ?? "").trim()}
            >
              {t("projects.create_modal.save")}
            </Button>
          </Space>
        </Modal.Header>
        <ProjectName
          name={name}
          setName={setName}
          error={error}
          onSaveName={onSaveName}
          onSubmit={onCreate}
          description={description}
          setDescription={setDescription}
          projectType={projectType}
          setProjectType={setProjectType}
          projectTypeOptions={projectTypeOptions}
        />
      </div>
    </Modal>
  );
};
