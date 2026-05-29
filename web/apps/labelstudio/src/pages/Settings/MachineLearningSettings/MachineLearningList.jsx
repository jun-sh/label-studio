import { formatDistanceToNow, format, parseISO } from "date-fns";
import { useCallback, useContext, useMemo } from "react";
import { useTranslation } from "react-i18next";

import truncate from "truncate-middle";
import { Menu } from "../../../components";
import { Button, Dropdown } from "@humansignal/ui";
import { confirm } from "../../../components/Modal/Modal";
import { Oneof } from "../../../components/Oneof/Oneof";
import { IconEllipsis } from "@humansignal/icons";
import { Tooltip } from "@humansignal/ui";
import { ApiContext } from "../../../providers/ApiProvider";
import { cn } from "../../../utils/bem";
import { getDateFnsLocale } from "../../../utils/dateFnsLocale";

import "./MachineLearningList.scss";

export const MachineLearningList = ({ backends, fetchBackends, onEdit, onTestRequest, onStartTraining }) => {
  const api = useContext(ApiContext);

  const onDeleteModel = useCallback(
    async (backend) => {
      await api.callApi("deleteMLBackend", {
        params: {
          pk: backend.id,
        },
      });
      await fetchBackends();
    },
    [fetchBackends, api],
  );

  return (
    <div>
      {backends.map((backend) => (
        <BackendCard
          key={backend.id}
          backend={backend}
          onStartTrain={onStartTraining}
          onDelete={onDeleteModel}
          onEdit={onEdit}
          onTestRequest={onTestRequest}
        />
      ))}
    </div>
  );
};

const BackendCard = ({ backend, onStartTrain, onEdit, onDelete, onTestRequest }) => {
  const { t, i18n } = useTranslation("common");
  const dateLocale = useMemo(() => getDateFnsLocale(i18n.language), [i18n.language]);

  const confirmDelete = useCallback(
    (item) => {
      confirm({
        title: t("dialogs.delete_ml_backend_title"),
        body: t("dialogs.cannot_undo_body"),
        buttonLook: "destructive",
        onOk() {
          onDelete?.(item);
        },
      });
    },
    [onDelete, t],
  );

  const rootClass = cn("backend-card");

  return (
    <div className={rootClass.toClassName()}>
      <div className={rootClass.elem("title-container").toClassName()}>
        <div>
          <BackendState backend={backend} />
          <div className={rootClass.elem("title").toClassName()}>{backend.title}</div>
        </div>

        <div className={rootClass.elem("menu").toClassName()}>
          <Dropdown.Trigger
            align="right"
            content={
              <Menu size="medium" contextual>
                <Menu.Item onClick={() => onEdit(backend)}>{t("ml_ui.edit")}</Menu.Item>
                <Menu.Item onClick={() => onTestRequest(backend)}>{t("ml_ui.send_test_request")}</Menu.Item>
                <Menu.Item onClick={() => onStartTrain(backend)}>{t("ml_ui.start_training")}</Menu.Item>
                <Menu.Divider />
                <Menu.Item onClick={() => confirmDelete(backend)} isDangerous>
                  {t("ml_ui.delete")}
                </Menu.Item>
              </Menu>
            }
          >
            <Button look="string" size="small" className="!p-0" aria-label={t("ml_ui.model_options_aria")}>
              <IconEllipsis />
            </Button>
          </Dropdown.Trigger>
        </div>
      </div>

      <div className={rootClass.elem("meta").toClassName()}>
        <div className={rootClass.elem("group").toClassName()}>{truncate(backend.url, 20, 10, "...")}</div>
        <div className={rootClass.elem("group").toClassName()}>
          <Tooltip title={format(parseISO(backend.created_at), "yyyy-MM-dd HH:mm:ss", { locale: dateLocale })}>
            <span>
              {t("ml_ui.created_prefix")}&nbsp;
              {formatDistanceToNow(parseISO(backend.created_at), {
                addSuffix: true,
                locale: dateLocale,
              })}
            </span>
          </Tooltip>
        </div>
      </div>
    </div>
  );
};

const BackendState = ({ backend }) => {
  const { state } = backend;

  return (
    <div className={cn("ml").elem("status").toClassName()}>
      <span className={cn("ml").elem("indicator").mod({ state }).toClassName()} />
      <Oneof value={state} className={cn("ml").elem("status-label").toClassName()}>
        <span case="DI">Disconnected</span>
        <span case="CO">Connected</span>
        <span case="ER">Error</span>
        <span case="TR">Training</span>
        <span case="PR">Predicting</span>
      </Oneof>
    </div>
  );
};
