import { IconChevronDown } from "@humansignal/icons";
import { isStarterCloudPlan } from "@humansignal/core";
import { useTranslation } from "react-i18next";
import { cn } from "../../../utils/bem";
import { ErrorBox } from "../../Common/ErrorBox";
import { FieldsButton } from "../../Common/FieldsButton";
import { FiltersPane } from "../../Common/FiltersPane";
import { Icon } from "../../Common/Icon/Icon";
import { Interface } from "../../Common/Interface";
import { ExportButton, ImportButton } from "../../Common/SDKButtons";
import { Tooltip } from "@humansignal/ui";
import { ActionsButton } from "./ActionsButton";
import { DensityToggle } from "./DensityToggle";
import { GridWidthButton } from "./GridWidthButton";
import { LabelButton } from "./LabelButton";
import { LoadingPossum } from "./LoadingPossum";
import { OrderButton } from "./OrderButton";
import { RefreshButton } from "./RefreshButton";
import { ViewToggle } from "./ViewToggle";

const style = {
  minWidth: "80px",
  justifyContent: "space-between",
};

/**
 * Checks for Starter Cloud trial expiration.
 * If expired it renders disabled Import button with a tooltip.
 */
const ImportButtonWithChecks = ({ size }) => {
  const { t } = useTranslation("common");
  const simpleButton = (
    <ImportButton size={size}>{t("dm.toolbar.import")}</ImportButton>
  );
  const isOpenSource = !window.APP_SETTINGS.billing;
  const isStarterCloud = isStarterCloudPlan();

  if (isOpenSource || !isStarterCloud) return simpleButton;

  const isTrialExpired = window.APP_SETTINGS.billing.checks?.is_license_expired;
  const subscriptionPeriodEnd = window.APP_SETTINGS.subscription?.current_period_end;
  const isStarterCloudExpiredTrial = isStarterCloud && isTrialExpired && !subscriptionPeriodEnd;
  const isStarterCloudExpiredSubscription =
    isStarterCloud && subscriptionPeriodEnd && new Date(subscriptionPeriodEnd) < new Date();
  const isStarterCloudExpired = isStarterCloudExpiredTrial || isStarterCloudExpiredSubscription;

  if (!isStarterCloudExpired) return simpleButton;

  return (
    <Tooltip
      title={t("dm.toolbar.import_upgrade_tooltip")}
      style={{
        maxWidth: 200,
        textAlign: "center",
      }}
    >
      <div className={cn("button-wrapper").toClassName()}>
        <ImportButton disabled size={size}>
          {t("dm.toolbar.import")}
        </ImportButton>
      </div>
    </Tooltip>
  );
};

const ColumnsInstrument = ({ size }) => {
  const { t } = useTranslation("common");
  const iconProps = {
    style: {
      marginRight: 4,
    },
    icon: IconChevronDown,
  };
  return (
    <FieldsButton
      wrapper={FieldsButton.Checkbox}
      trailingIcon={<Icon {...iconProps} />}
      title={t("dm.toolbar.columns")}
      size={size}
      style={style}
      openUpwardForShortViewport={false}
    />
  );
};

const ExportInstrument = ({ size }) => {
  const { t } = useTranslation("common");
  return (
    <Interface name="export">
      <ExportButton size={size}>{t("dm.toolbar.export")}</ExportButton>
    </Interface>
  );
};

export const instruments = {
  "view-toggle": ({ size }) => {
    return <ViewToggle size={size} style={style} />;
  },
  "density-toggle": ({ size }) => {
    return <DensityToggle size={size} />;
  },
  columns: ({ size }) => {
    return <ColumnsInstrument size={size} />;
  },
  filters: ({ size }) => {
    return <FiltersPane size={size} style={style} />;
  },
  ordering: ({ size }) => {
    return <OrderButton size={size} style={style} />;
  },
  "grid-size": ({ size }) => {
    return <GridWidthButton size={size} />;
  },
  refresh: ({ size }) => {
    return <RefreshButton size={size} />;
  },
  "loading-possum": () => {
    return <LoadingPossum />;
  },
  "label-button": ({ size }) => {
    return <LabelButton size={size} />;
  },
  actions: ({ size }) => {
    return <ActionsButton size={size} style={style} />;
  },
  "error-box": () => {
    return <ErrorBox />;
  },
  "import-button": ({ size }) => {
    return (
      <Interface name="import">
        <ImportButtonWithChecks size={size} />
      </Interface>
    );
  },
  "export-button": ({ size }) => {
    return <ExportInstrument size={size} />;
  },
};
