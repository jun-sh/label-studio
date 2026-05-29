import { useCallback, useContext, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { format, formatDistanceToNow, parseISO } from "date-fns";
import { Menu } from "../../../components";
import { Button, Dropdown } from "@humansignal/ui";
import { IconInfoOutline, IconPredictions, IconEllipsis } from "@humansignal/icons";
import { Tooltip } from "@humansignal/ui";
import { confirm } from "../../../components/Modal/Modal";
import { ApiContext } from "../../../providers/ApiProvider";
import { cn } from "../../../utils/bem";
import { getDateFnsLocale } from "../../../utils/dateFnsLocale";

import "./PredictionsList.scss";

export const PredictionsList = ({ project, versions, fetchVersions }) => {
  const api = useContext(ApiContext);

  const onDelete = useCallback(
    async (version) => {
      await api.callApi("deletePredictions", {
        params: {
          pk: project.id,
        },
        body: {
          model_version: version.model_version,
        },
      });
      await fetchVersions();
    },
    [fetchVersions, api],
  );

  return (
    <div style={{ maxWidth: 680 }}>
      {versions.map((v) => (
        <VersionCard key={v.model_version} version={v} onDelete={onDelete} />
      ))}
    </div>
  );
};

const VersionCard = ({ version, selected, onSelect, editable, onDelete }) => {
  const { t, i18n } = useTranslation("common");
  const dateLocale = useMemo(() => getDateFnsLocale(i18n.language), [i18n.language]);
  const rootClass = cn("prediction-card");

  const confirmDelete = useCallback(
    (ver) => {
      confirm({
        title: t("dialogs.delete_predictions_title"),
        body: t("dialogs.cannot_undo_body"),
        buttonLook: "destructive",
        onOk() {
          onDelete?.(ver);
        },
      });
    },
    [onDelete, t],
  );

  return (
    <div className={rootClass.toClassName()}>
      <div>
        <div className={rootClass.elem("title").toClassName()}>
          {version.model_version}
          {version.model_version === "undefined" && (
            <Tooltip title={t("predictions_ui.model_version_undefined_tooltip")}>
              <IconInfoOutline className={cn("help-icon").toClassName()} width="14" height="14" />
            </Tooltip>
          )}
        </div>
        <div className={rootClass.elem("meta").toClassName()}>
          <div className={rootClass.elem("group").toClassName()}>
            <IconPredictions />
            &nbsp;{version.count}
          </div>
          <div className={rootClass.elem("group").toClassName()}>
            {t("predictions_ui.last_prediction_created")}&nbsp;
            <Tooltip title={format(parseISO(version.latest), "yyyy-MM-dd HH:mm:ss", { locale: dateLocale })}>
              <span>
                {formatDistanceToNow(parseISO(version.latest), {
                  addSuffix: true,
                  locale: dateLocale,
                })}
              </span>
            </Tooltip>
          </div>
        </div>
      </div>
      <div className={rootClass.elem("menu").toClassName()}>
        <Dropdown.Trigger
          align="right"
          content={
            <Menu size="medium" contextual>
              <Menu.Item onClick={() => confirmDelete(version)} isDangerous>
                {t("predictions_ui.delete")}
              </Menu.Item>
            </Menu>
          }
        >
          <Button look="string">
            <IconEllipsis />
          </Button>
        </Dropdown.Trigger>
      </div>
    </div>
  );
};
