import chr from "chroma-js";
import { format } from "date-fns";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { IconCheck, IconEllipsis, IconMinus, IconSparks } from "@humansignal/icons";
import { Userpic, Button, Dropdown, Tooltip } from "@humansignal/ui";
import { Menu, Pagination } from "../../components";
import { cn } from "../../utils/bem";
import { getDateFnsLocale } from "../../utils/dateFnsLocale";
import { absoluteURL } from "../../utils/helpers";
import { isEmbodiedAnnotateProject, stripEmbodiedMarkerFromDescription } from "../EmbodiedAnnotate/embodiedAnnotate";
import { isLerobotQcProject, stripLerobotQcMarkerFromDescription } from "../LerobotQc/lerobotQc";
import { ProjectStateChip } from "@humansignal/app-common";

const DEFAULT_CARD_COLORS = ["#FFFFFF", "#FDFDFC"];

export const ProjectsList = ({ projects, currentPage, totalItems, loadNextPage, pageSize }) => {
  const { t } = useTranslation("common");
  return (
    <>
      <div className={cn("projects-page").elem("list").toClassName()}>
        {projects.map((project) => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>
      <div className={cn("projects-page").elem("pages").toClassName()}>
        <Pagination
          name="projects-list"
          label={t("projects.pagination_label")}
          page={currentPage}
          totalItems={totalItems}
          urlParamName="page"
          pageSize={pageSize}
          pageSizeOptions={[10, 30, 50, 100]}
          onPageLoad={(page, pageSize) => loadNextPage(page, pageSize)}
        />
      </div>
    </>
  );
};

export const EmptyProjectsList = ({ openModal }) => {
  const { t } = useTranslation("common");
  return (
    <div className={cn("empty-projects-page").toClassName()}>
      <img
        alt={t("projects.empty_alt")}
        className={cn("empty-projects-page").elem("heidi").toClassName()}
        src={absoluteURL("/static/images/opossum_looking.png")}
      />
      <h1 className={cn("empty-projects-page").elem("header").toClassName()}>{t("projects.empty_title")}</h1>
      <p>{t("projects.empty_description")}</p>
      <Button onClick={openModal} className="my-8" aria-label={t("projects.create_aria")}>
        {t("projects.create_project")}
      </Button>
    </div>
  );
};

const ProjectCard = ({ project }) => {
  const { t, i18n } = useTranslation("common");
  const dateLocale = useMemo(() => getDateFnsLocale(i18n.language), [i18n.language]);
  const color = useMemo(() => {
    return DEFAULT_CARD_COLORS.includes(project.color) ? null : project.color;
  }, [project]);

  const cardTitle =
    project.title === "Demo Project" ? t("projects.demo_card_title") : (project.title ?? t("projects.new_project"));
  const projectColors = useMemo(() => {
    const textColor =
      color && chr(color).luminance() > 0.3
        ? "var(--color-neutral-inverted-content)"
        : "var(--color-neutral-inverted-content)"; // Determine text color based on luminance
    return color
      ? {
          "--header-color": color,
          "--background-color": chr(color).alpha(0.2).css(),
          "--text-color": textColor,
          "--border-color": chr(color).alpha(0.5).css(),
        }
      : {};
  }, [color]);

  const projectPath = isEmbodiedAnnotateProject(project)
    ? `/projects/${project.id}/embodied`
    : isLerobotQcProject(project)
      ? `/projects/${project.id}/lerobot-qc`
      : `/projects/${project.id}/data`;
  const embodied = isEmbodiedAnnotateProject(project);
  const lerobotQc = isLerobotQcProject(project);
  const customPipeline = embodied || lerobotQc;

  return (
    <div className={cn("projects-page").elem("link").toClassName()}>
      <div className={cn("project-card").mod({ colored: !!color }).toClassName()} style={projectColors}>
        <NavLink
          to={projectPath}
          className={cn("project-card").elem("nav-link").toClassName()}
          aria-label={cardTitle}
          {...(!customPipeline ? { "data-external": true } : {})}
        />
        <div className={cn("project-card").elem("header").toClassName()}>
          <div className={cn("project-card").elem("title").toClassName()}>
            <div className={cn("project-card").elem("title-text-wrapper").toClassName()}>
              <Tooltip title={cardTitle}>
                <div className={cn("project-card").elem("title-text").toClassName()}>
                  {cardTitle}
                </div>
              </Tooltip>
            </div>

            <div className={cn("project-card").elem("menu").toClassName()}>
              <Dropdown.Trigger
                content={
                  <Menu contextual>
                    <Menu.Item href={`/projects/${project.id}/settings`}>{t("projects.settings")}</Menu.Item>
                    {!embodied && (
                      <Menu.Item href={`/projects/${project.id}/data?labeling=1`}>{t("projects.label")}</Menu.Item>
                    )}
                  </Menu>
                }
              >
                <Button size="smaller" look="string" aria-label={t("projects.project_options_aria")}>
                  <IconEllipsis />
                </Button>
              </Dropdown.Trigger>
            </div>

            {project.state && (
              <div className={cn("project-card").elem("state-chip").toClassName()}>
                <ProjectStateChip state={project.state} projectId={project.id} interactive={false} />
              </div>
            )}
          </div>
          <div className={cn("project-card").elem("summary").toClassName()}>
            <div className={cn("project-card").elem("annotation").toClassName()}>
              <div className={cn("project-card").elem("total").toClassName()}>
                {project.finished_task_number} / {project.task_number}
              </div>
              <div className={cn("project-card").elem("detail").toClassName()}>
                <div className={cn("project-card").elem("detail-item").mod({ type: "completed" }).toClassName()}>
                  <IconCheck className={cn("project-card").elem("icon").toClassName()} />
                  {project.total_annotations_number}
                </div>
                <div className={cn("project-card").elem("detail-item").mod({ type: "rejected" }).toClassName()}>
                  <IconMinus className={cn("project-card").elem("icon").toClassName()} />
                  {project.skipped_annotations_number}
                </div>
                <div className={cn("project-card").elem("detail-item").mod({ type: "predictions" }).toClassName()}>
                  <IconSparks className={cn("project-card").elem("icon").toClassName()} />
                  {project.total_predictions_number}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className={cn("project-card").elem("description").toClassName()}>
          {embodied
            ? stripEmbodiedMarkerFromDescription(project.description)
            : lerobotQc
              ? stripLerobotQcMarkerFromDescription(project.description)
              : project.description}
        </div>
        <div className={cn("project-card").elem("info").toClassName()}>
          <div className={cn("project-card").elem("created-date").toClassName()}>
            {format(new Date(project.created_at), "dd MMM yyyy, HH:mm", { locale: dateLocale })}
          </div>
          <div className={cn("project-card").elem("created-by").toClassName()}>
            <Userpic src="#" user={project.created_by} showUsernameTooltip />
          </div>
        </div>
      </div>
    </div>
  );
};
