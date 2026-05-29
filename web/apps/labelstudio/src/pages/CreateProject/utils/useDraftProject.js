import { projectAtom } from "apps/labelstudio/src/providers/ProjectProvider";
import { useAtom } from "jotai";
import React, { useEffect } from "react";
import i18n from "../../../i18n/config";
import { useAPI } from "../../../providers/ApiProvider";

export const useDraftProject = () => {
  const api = useAPI();
  const [project, setProject] = useAtom(projectAtom);

  const fetchDraftProject = React.useCallback(async () => {
    const response = await api.callApi("projects");

    // always create the new one
    const projects = response?.results ?? [];
    const lastIndex = projects.length;
    let projectNumber = lastIndex + 1;
    let projectName = i18n.t("projects.draft_title", { number: projectNumber });

    // dirty hack to get proper non-duplicate name
    while (projects.find(({ title }) => title === projectName)) {
      projectNumber++;
      projectName = i18n.t("projects.draft_title", { number: projectNumber });
    }

    const draft = await api.callApi("createProject", {
      body: {
        title: projectName,
        is_draft: true,
      },
    });

    if (draft) setProject(draft);
  }, []);

  useEffect(() => {
    fetchDraftProject();
  }, []);

  return { project, setProject };
};
