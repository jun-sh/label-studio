import {
  IconBoundingBox,
  IconClockTimeFourOutline,
  IconCursor,
  IconList,
  IconOutlinerEyeClosed,
  IconOutlinerEyeOpened,
  IconPredictions,
  IconSortDown,
  IconSortUp,
  IconTimelineRegion,
} from "@humansignal/icons";
import { Button } from "@humansignal/ui";
import { type FC, useCallback, useContext, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Dropdown } from "@humansignal/ui";
// eslint-disable-next-line
// @ts-ignore
import { Menu } from "../../../common/Menu/Menu";
import { cn } from "../../../utils/bem";
import { SidePanelsContext } from "../SidePanelsContext";
import "./ViewControls.scss";
import { observer } from "mobx-react";

export type GroupingOptions = "manual" | "label" | "type";

export type OrderingOptions = "score" | "date" | "mediaStartTime";

export type OrderingDirection = "asc" | "desc";

interface ViewControlsProps {
  ordering: OrderingOptions;
  orderingDirection?: OrderingDirection;
  regions: any;
  onOrderingChange: (ordering: OrderingOptions) => void;
  onGroupingChange: (grouping: GroupingOptions) => void;
}

const mediaStartTimeSupportedTags = [
  ["labels", "audio"],
  ["labels", "videorectangle", "video"],
  ["timelinelabels", "video"],
  ["timeserieslabels", "timeseries"],
];

export const ViewControls: FC<ViewControlsProps> = observer(
  ({ ordering, regions, orderingDirection, onOrderingChange, onGroupingChange }) => {
    const { t } = useTranslation("common");
    const grouping = regions.group;
    const context = useContext(SidePanelsContext);

    // Check labeling configuration for media-time-capable object tags
    const mediaTimeSupport: boolean | null = useMemo(() => {
      const names = regions.annotation?.names;
      if (!names || names.size === 0) return null;

      const tags = Array.from(names.values());
      // Check if all tag types from the tuple exist in the configuration
      return mediaStartTimeSupportedTags.some((requiredTagTypes) => {
        return requiredTagTypes.every((requiredType) => tags.some((tag: any) => tag?.type === requiredType));
      });
    }, [regions.annotation?.names]);

    // Auto-fallback to "date" if current ordering is "mediaStartTime" but no media-time support in config
    useEffect(() => {
      if (ordering === "mediaStartTime" && mediaTimeSupport === false) {
        onOrderingChange("date");
      }
    }, [ordering, mediaTimeSupport, onOrderingChange]);

    const getGroupingLabels = (value: GroupingOptions): LabelInfo => {
      switch (value) {
        case "manual":
          return {
            label: (
              <>
                <IconList /> {t("editor.view_controls.group_manual_menu")}
              </>
            ),
            selectedLabel: t("editor.view_controls.manual"),
            icon: <IconList width={16} height={16} />,
            tooltip: t("editor.view_controls.tooltip_manual"),
          };
        case "label":
          return {
            label: (
              <>
                <IconBoundingBox /> {t("editor.view_controls.group_by_label_menu")}
              </>
            ),
            selectedLabel: t("editor.view_controls.by_label"),
            icon: <IconBoundingBox width={16} height={16} />,
            tooltip: t("editor.view_controls.tooltip_by_label"),
          };
        case "type":
          return {
            label: (
              <>
                <IconCursor /> {t("editor.view_controls.group_by_tool_menu")}
              </>
            ),
            selectedLabel: t("editor.view_controls.by_tool"),
            icon: <IconCursor width={16} height={16} />,
            tooltip: t("editor.view_controls.tooltip_by_tool"),
          };
      }
    };

    const getOrderingLabels = (value: OrderingOptions): LabelInfo => {
      switch (value) {
        case "date":
          return {
            label: (
              <>
                <IconClockTimeFourOutline /> {t("editor.view_controls.order_by_time_menu")}
              </>
            ),
            selectedLabel: t("editor.view_controls.by_time"),
            icon: <IconClockTimeFourOutline width={16} height={16} />,
          };
        case "score":
          return {
            label: (
              <>
                <IconPredictions /> {t("editor.view_controls.order_by_score_menu")}
              </>
            ),
            selectedLabel: t("editor.view_controls.by_score"),
            icon: <IconPredictions width={16} height={16} />,
          };
        case "mediaStartTime":
          return {
            label: (
              <>
                <IconTimelineRegion /> {t("editor.view_controls.order_by_media_start_menu")}
              </>
            ),
            selectedLabel: t("editor.view_controls.by_media_start"),
            icon: <IconTimelineRegion width={16} height={16} />,
          };
      }
    };

    const renderOrderingDirectionIcon = orderingDirection === "asc" ? <IconSortUp /> : <IconSortDown />;

    return (
      <div className={cn("view-controls").mod({ collapsed: context.locked }).toClassName()}>
        <Grouping
          value={grouping}
          options={["manual", "type", "label"]}
          onChange={(value) => onGroupingChange(value)}
          readableValueForKey={getGroupingLabels}
        />
        {grouping === "manual" && (
          <div className={cn("view-controls").elem("sort").toClassName()}>
            <Grouping
              value={ordering}
              direction={orderingDirection}
              options={mediaTimeSupport ? ["score", "date", "mediaStartTime"] : ["score", "date"]}
              onChange={(value) => onOrderingChange(value)}
              readableValueForKey={getOrderingLabels}
              allowClickSelected
              extraIcon={renderOrderingDirectionIcon}
              width={230}
            />
          </div>
        )}
        <ToggleRegionsVisibilityButton regions={regions} />
      </div>
    );
  },
);

interface LabelInfo {
  label: string | React.ReactNode | JSX.Element;
  selectedLabel: string;
  icon: JSX.Element;
  tooltip?: string;
}

interface GroupingProps<T extends string> {
  value: T;
  options: T[];
  direction?: OrderingDirection;
  allowClickSelected?: boolean;
  onChange: (value: T) => void;
  readableValueForKey: (value: T) => LabelInfo;
  extraIcon?: JSX.Element;
  width?: number;
}

const Grouping = <T extends string>({
  value,
  options,
  direction,
  allowClickSelected,
  onChange,
  readableValueForKey,
  extraIcon,
  width = 200,
}: GroupingProps<T>) => {
  const readableValue = useMemo(() => {
    return readableValueForKey(value);
  }, [value]);

  const optionsList: [T, LabelInfo][] = useMemo(() => {
    return options.map((key) => [key, readableValueForKey(key)]);
  }, [options, readableValueForKey]);

  const dropdownContent = useMemo(() => {
    return (
      <Menu
        size="medium"
        style={{
          width,
          minWidth: width,
          borderRadius: 4,
        }}
        selectedKeys={[value]}
        allowClickSelected={allowClickSelected}
      >
        {optionsList.map(([key, label]) => (
          <GroupingMenuItem
            key={key}
            name={key}
            value={value}
            direction={direction}
            label={label}
            onChange={(value) => onChange(value)}
          />
        ))}
      </Menu>
    );
  }, [value, optionsList, readableValue, direction, onChange]);

  return (
    <Dropdown.Trigger content={dropdownContent} style={{ width }}>
      <Button
        variant="neutral"
        size="smaller"
        data-testid={`grouping-${value}`}
        look="string"
        leading={readableValue.icon}
        trailing={extraIcon}
      >
        {readableValue.selectedLabel}
      </Button>
    </Dropdown.Trigger>
  );
};

interface GroupingMenuItemProps<T extends string> {
  name: T;
  label: LabelInfo;
  value: T;
  direction?: OrderingDirection;
  onChange: (key: T) => void;
}

const GroupingMenuItem = <T extends string>({ value, name, label, direction, onChange }: GroupingMenuItemProps<T>) => {
  return (
    <Menu.Item name={name} onClick={() => onChange(name)}>
      <div className={cn("view-controls").elem("label").toClassName()}>
        {label.label}
        <DirectionIndicator direction={direction} name={name} value={value} />
      </div>
    </Menu.Item>
  );
};

interface DirectionIndicator {
  direction?: OrderingDirection;
  value: string;
  name: string;
  wrap?: boolean;
}

const DirectionIndicator: FC<DirectionIndicator> = ({ direction, value, name, wrap = true }) => {
  const content = direction === "asc" ? <IconSortUp /> : <IconSortDown />;

  if (!direction || value !== name) return null;
  if (!wrap) return content;

  return <span>{content}</span>;
};

interface ToggleRegionsVisibilityButton {
  regions: any;
}

const ToggleRegionsVisibilityButton = observer<FC<ToggleRegionsVisibilityButton>>(({ regions }) => {
  const { t } = useTranslation("common");
  const toggleRegionsVisibility = useCallback(
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      regions.toggleVisibility();
    },
    [regions],
  );

  const isDisabled = !regions?.regions?.length;
  const isAllHidden = !isDisabled && regions.isAllHidden;

  return (
    <Button
      variant="neutral"
      size="smaller"
      look="string"
      disabled={isDisabled}
      onClick={toggleRegionsVisibility}
      aria-label={isAllHidden ? t("editor.view_controls.show_all_regions") : t("editor.view_controls.hide_all_regions")}
      tooltip={isAllHidden ? t("editor.view_controls.show_all_regions") : t("editor.view_controls.hide_all_regions")}
    >
      {isAllHidden ? (
        <IconOutlinerEyeClosed width={16} height={16} />
      ) : (
        <IconOutlinerEyeOpened width={16} height={16} />
      )}
    </Button>
  );
});
