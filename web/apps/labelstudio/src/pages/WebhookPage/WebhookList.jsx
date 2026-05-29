import { IconCross, IconPencil, IconWebhook } from "@humansignal/icons";
import { Button, EmptyState, SimpleCard, Typography } from "@humansignal/ui";
import clsx from "clsx";
import { format } from "date-fns";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Toggle } from "../../components/Form";
import { useAPI } from "../../providers/ApiProvider";
import { getDateFnsLocale } from "../../utils/dateFnsLocale";
import { WebhookDeleteModal } from "./WebhookDeleteModal";
import { ABILITY, useAuth } from "@humansignal/core/providers/AuthProvider";

const WebhookListItem = ({ webhook, onSelectActive, onActiveChange, onDelete, canChangeWebhooks }) => {
  const { t, i18n } = useTranslation("common");
  const dateLocale = useMemo(() => getDateFnsLocale(i18n.language), [i18n.language]);

  return (
    <li
      className={clsx(
        "flex justify-between items-center p-2 text-base border border-neutral-border rounded-lg group",
        canChangeWebhooks && "hover:bg-neutral-surface",
      )}
    >
      <div>
        <div className="flex items-center">
          <div>
            <Toggle
              name={webhook.id}
              checked={webhook.is_active}
              onChange={onActiveChange}
              disabled={!canChangeWebhooks}
            />
          </div>
          <div
            className={clsx(
              "max-w-[370px] overflow-hidden text-ellipsis font-medium ml-2",
              canChangeWebhooks && "cursor-pointer",
            )}
            onClick={canChangeWebhooks ? () => onSelectActive(webhook.id) : undefined}
          >
            {webhook.url}
          </div>
        </div>
        <div className="text-neutral-content-subtler text-sm mt-1">
          {t("webhooks.created_prefix")}{" "}
          {format(new Date(webhook.created_at), "dd MMM yyyy, HH:mm", { locale: dateLocale })}
        </div>
      </div>
      {canChangeWebhooks && (
        <div className="hidden group-hover:flex gap-2">
          <Button variant="primary" look="outlined" onClick={() => onSelectActive(webhook.id)} icon={<IconPencil />}>
            {t("settings_webhooks.edit")}
          </Button>
          <Button
            variant="negative"
            look="outlined"
            onClick={() =>
              WebhookDeleteModal({
                onDelete,
              })
            }
            icon={<IconCross />}
          >
            {t("settings_webhooks.delete")}
          </Button>
        </div>
      )}
    </li>
  );
};

const WebhookList = ({ onSelectActive, onAddWebhook, webhooks, fetchWebhooks }) => {
  const { t } = useTranslation("common");
  const api = useAPI();
  const { permissions } = useAuth();
  const canChangeWebhooks = permissions.can(ABILITY.can_change_webhooks);

  const productName = t("settings_webhooks.app_product_name");

  if (webhooks === null) return <></>;

  const onActiveChange = useCallback(async (event) => {
    const value = event.target.checked;

    await api.callApi("updateWebhook", {
      params: {
        pk: event.target.name,
      },
      body: {
        is_active: value,
      },
    });
    await fetchWebhooks();
  }, []);

  return (
    <>
      <header className="mb-base">
        <Typography variant="headline" size="medium" className="mb-tight">
          {t("breadcrumbs.webhooks")}
        </Typography>
        {webhooks.length > 0 && (
          <Typography size="small" className="text-neutral-content-subtler">
            {t("settings_webhooks.list_intro", { product: productName })}
          </Typography>
        )}
      </header>
      <div className="w-full">
        {webhooks.length === 0 ? (
          <SimpleCard title="" className="bg-primary-background border-primary-border-subtler p-base">
            <EmptyState
              size="medium"
              variant="primary"
              icon={<IconWebhook />}
              title={t("settings_webhooks.empty_title")}
              description={t("settings_webhooks.empty_description")}
              actions={
                canChangeWebhooks ? (
                  <Button variant="primary" look="filled" onClick={onAddWebhook}>
                    {t("settings_webhooks.add_webhook")}
                  </Button>
                ) : (
                  <Typography variant="body" size="small">
                    {t("settings_webhooks.contact_admin_create")}
                  </Typography>
                )
              }
              footer={
                !window.APP_SETTINGS.whitelabel_is_active && (
                  <Typography variant="label" size="small" className="text-neutral-content-subtle">
                    {t("settings_webhooks.contact_admin_footer")}
                  </Typography>
                )
              }
            />
          </SimpleCard>
        ) : (
          <ul className="space-y-4 mt-wide">
            {webhooks.map((obj) => (
              <WebhookListItem
                key={obj.id}
                webhook={obj}
                onSelectActive={onSelectActive}
                onActiveChange={onActiveChange}
                onDelete={async () => {
                  await api.callApi("deleteWebhook", {
                    params: { pk: obj.id },
                  });
                  await fetchWebhooks();
                }}
                canChangeWebhooks={canChangeWebhooks}
              />
            ))}
          </ul>
        )}
      </div>
      {webhooks.length > 0 && canChangeWebhooks && (
        <div className="flex justify-end w-full mt-base">
          <Button variant="primary" look="filled" onClick={onAddWebhook}>
            {t("settings_webhooks.add_webhook")}
          </Button>
        </div>
      )}
    </>
  );
};

export default WebhookList;
