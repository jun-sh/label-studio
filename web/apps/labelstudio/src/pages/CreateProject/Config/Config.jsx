import React, { useEffect, useMemo, useState, useRef } from "react";
import CM from "codemirror";
import { Button, cnm } from "@humansignal/ui";
import { IconTrash, IconInfoOutline } from "@humansignal/icons";
import { ToggleItems } from "../../../components";
import { Form, Input } from "../../../components/Form";
import { useAPI } from "../../../providers/ApiProvider";
import { cn } from "../../../utils/bem";
import { Palette } from "../../../utils/colors";
import { FF_UNSAVED_CHANGES, isFF } from "../../../utils/feature-flags";
import { colorNames } from "./colors";
import "./Config.scss";
import { Preview } from "./Preview";
import { ff, LARGE_CONFIG_TAG_THRESHOLD, countConfigTags } from "@humansignal/core";
import { Trans, useTranslation } from "react-i18next";
import { DEFAULT_COLUMN, EMPTY_CONFIG, isEmptyConfig, Template } from "./Template";
import { TemplatesList } from "./TemplatesList";

import tags from "@humansignal/core/lib/utils/schema/tags.json";
import { UnsavedChanges } from "./UnsavedChanges";
import { Checkbox, CodeEditor, Select } from "@humansignal/ui";
import { snakeCase } from "@humansignal/core";
import { useConfigResizer } from "./useConfigResizer";
import { EditorResizer } from "./EditorResizer";
import { SamClassesPanel } from "../../Settings/SamClassesPanel";

const wizardClass = cn("wizard");
const configClass = cn("configure");

/**
 * AdaptivePreview - Shows manual update banner for large configs,
 * normal auto-updating preview for smaller configs.
 *
 * When FF_PREVIEW_PERFORMANCE is enabled and config has >= 200 tags,
 * shows a banner with "Update Preview" button instead of auto-updating.
 *
 * Controlled by FF_PREVIEW_PERFORMANCE feature flag.
 *
 * Wrapped in React.memo to prevent unnecessary re-renders when parent re-renders.
 */
const AdaptivePreview = React.memo(({ config, hasPendingUpdate, onUpdatePreview, isUpdating, ...previewProps }) => {
  const { t } = useTranslation("common");
  const isFeatureEnabled = ff.isActive(ff.FF_PREVIEW_PERFORMANCE);

  // Memoize tag count calculation to avoid re-computing on every render
  const tagCount = useMemo(() => countConfigTags(config || ""), [config]);
  const isLargeConfig = tagCount >= LARGE_CONFIG_TAG_THRESHOLD;

  // Only show manual update banner when FF is ON and config is large and there are pending updates
  const showManualUpdateBanner = isFeatureEnabled && isLargeConfig && hasPendingUpdate;

  if (showManualUpdateBanner) {
    return (
      <div className={configClass.elem("preview-container").toClassName()}>
        <div className={configClass.elem("preview-info-banner").toClassName()}>
          <IconInfoOutline width={16} height={16} />
          <span>{t("labeling_config.large_config_message")}</span>
          <Button size="small" onClick={onUpdatePreview} waiting={isUpdating} disabled={isUpdating}>
            {isUpdating ? t("labeling_config.updating") : t("labeling_config.update_preview")}
          </Button>
        </div>
        <Preview config={config} {...previewProps} />
      </div>
    );
  }

  return <Preview config={config} {...previewProps} />;
});

const EmptyConfigPlaceholder = () => {
  const { t } = useTranslation("common");
  return (
    <div className={configClass.elem("empty-config").toClassName()}>
      <p>{t("labeling_config.empty_p1")}</p>
      <p>
        {t("labeling_config.empty_p2_before")}
        <a href="https://labelstud.io/tags/" target="_blank" rel="noreferrer">
          {t("labeling_config.empty_p2_link")}
        </a>
        {t("labeling_config.empty_p2_after")}
      </p>
    </div>
  );
};

const Label = ({ label, template, color }) => {
  const { t } = useTranslation("common");
  const value = label.getAttribute("value");

  return (
    <li
      className={cnm(
        configClass
          .elem("label")
          .mod({ choice: label.tagName === "Choice" })
          .toClassName(),
        "group",
      )}
    >
      <span className={cnm(configClass.elem("label-text").toClassName(), "flex")}>
        <label style={{ background: color }}>
          <Input
            type="color"
            className={configClass.elem("label-color").toClassName()}
            value={colorNames[color] || color}
            onChange={(e) => template.changeLabel(label, { background: e.target.value })}
          />
        </label>
        <span>{value}</span>
      </span>
      <Button
        type="button"
        look="string"
        size="smaller"
        variant="negative"
        onClick={() => template.removeLabel(label)}
        aria-label={t("labeling_config.delete_label_aria")}
        className="hidden !p-0 z-10 absolute right-0 [&_span]:!p-0 group-hover:inline-flex"
        leading={<IconTrash className="w-4 h-4 fill-[currentColor]" />}
      />
    </li>
  );
};

const ConfigureControl = ({ control, template }) => {
  const { t } = useTranslation("common");
  const refLabels = React.useRef();
  const tagname = control.tagName;

  if (tagname !== "Choices" && !tagname.endsWith("Labels")) return null;
  const palette = Palette();

  const onAddLabels = () => {
    if (!refLabels.current) return;
    template.addLabels(control, refLabels.current.value);
    refLabels.current.value = "";
  };
  const onKeyPress = (e) => {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      onAddLabels();
    }
  };

  return (
    <div className={configClass.elem("labels").toClassName()}>
      <form className={configClass.elem("add-labels").toClassName()} action="">
        <h4>{tagname === "Choices" ? t("labeling_config.add_choices_heading") : t("labeling_config.add_label_names_heading")}</h4>
        <span>{t("labeling_config.add_labels_hint")}</span>
        <textarea
          name="labels"
          id=""
          cols="50"
          rows="5"
          ref={refLabels}
          onKeyPress={onKeyPress}
          className="lsf-textarea-ls p-2 px-3"
        />
        <Button type="button" size="small" look="outlined" onClick={onAddLabels} aria-label={t("labeling_config.add_labels_aria")}>
          {t("labeling_config.add_button")}
        </Button>
      </form>
      <div className={configClass.elem("current-labels").toClassName()}>
        <h3>
          {tagname === "Choices"
            ? t("labeling_config.choices_with_count", { count: control.children.length })
            : t("labeling_config.labels_with_count", { count: control.children.length })}
        </h3>
        <ul>
          {Array.from(control.children).map((label) => (
            <Label
              label={label}
              template={template}
              key={label.getAttribute("value")}
              color={label.getAttribute("background") || palette.next().value}
            />
          ))}
        </ul>
      </div>
    </div>
  );
};

const ConfigureSettings = ({ template }) => {
  const { t } = useTranslation("common");
  const { settings } = template;

  if (!settings) return null;
  const keys = Object.keys(settings);

  const settingTitle = (options) => {
    if (options.titleKey) return t(options.titleKey);
    return options.title ?? "";
  };

  const settingOptions = (options) => {
    if (!Array.isArray(options.type)) return options.type;
    return options.type.map((value) => ({
      value,
      label: t(`labeling_config.tag_settings.label_position.${value}`, { defaultValue: value }),
    }));
  };

  const items = keys.map((key) => {
    const options = settings[key];
    const type = Array.isArray(options.type) ? Array : options.type;
    const $object = options.object;
    const $tag = options.control ? options.control : $object;
    const title = settingTitle(options);

    if (!$tag) return null;
    if (options.when && !options.when($tag)) return;
    let value = false;

    if (options.value) value = options.value($tag);
    else if (typeof options.param === "string") value = $tag.getAttribute(options.param);
    if (value === "true") value = true;
    if (value === "false") value = false;
    let onChange;
    let size;

    switch (type) {
      case Array:
        onChange = (val) => {
          if (typeof options.param === "function") {
            options.param($tag, val);
          } else {
            $object.setAttribute(options.param, val);
          }
          template.render();
        };
        return (
          <li key={key}>
            <Select
              triggerClassName="border"
              value={value}
              onChange={onChange}
              options={settingOptions(options)}
              label={title}
              isInline={true}
              dataTestid={`select-trigger-${title.replace(/\s+/g, "-").replace(":", "").toLowerCase()}-${value}`}
            />
          </li>
        );
      case Boolean:
        onChange = (e) => {
          if (typeof options.param === "function") {
            options.param($tag, e.target.checked);
          } else {
            $object.setAttribute(options.param, e.target.checked ? "true" : "false");
          }
          template.render();
        };
        return (
          <li key={key}>
            <Checkbox checked={value} onChange={onChange}>
              {title}
            </Checkbox>
          </li>
        );
      case String:
      case Number:
        size = options.type === Number ? 5 : undefined;
        onChange = (e) => {
          if (typeof options.param === "function") {
            options.param($tag, e.target.value);
          } else {
            $object.setAttribute(options.param, e.target.value);
          }
          template.render();
        };
        return (
          <li key={key}>
            <label>
              {title} <Input type="text" onInput={onChange} value={value} size={size} />
            </label>
          </li>
        );
    }
  });

  // check for active settings
  if (!items.filter(Boolean).length) return null;

  return (
    <ul className={configClass.elem("settings").toClassName()}>
      <li>
        <h4>{t("labeling_config.configure_settings_heading")}</h4>
        <ul className={configClass.elem("object-settings").toClassName()}>{items}</ul>
      </li>
    </ul>
  );
};

// configure value source for `obj` object tag
const ConfigureColumn = ({ template, obj, columns }) => {
  const { t } = useTranslation("common");
  const valueAttr = obj.hasAttribute("valueList") ? "valueList" : "value";
  const value = obj.getAttribute(valueAttr)?.replace(/^\$/, "");
  // if there is a value set already and it's not in the columns
  // or data was not uploaded yet
  const [isManual, setIsManual] = useState(!!value && !columns?.includes(value));
  // value is stored in state to make input conrollable
  // changes will be sent by Enter and blur
  const [newValue, setNewValue] = useState(`$${value}`);

  // update local state when external value changes
  useEffect(() => {
    setNewValue(`$${value}`);
  }, [value]);

  const updateValue = (value) => {
    const newValue = value.replace(/^\$/, "");

    obj.setAttribute(valueAttr, `$${newValue}`);
    template.render();
  };

  const selectValue = (value) => {
    if (value === "-") {
      setIsManual(true);
      return;
    }
    if (isManual) {
      setIsManual(false);
    }

    updateValue(value);
  };

  const handleChange = (e) => {
    const newValue = e.target.value.replace(/^\$/, "");

    setNewValue(`$${newValue}`);
  };

  const handleBlur = () => {
    updateValue(newValue);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      updateValue(e.target.value);
    }
  };

  const options = useMemo(() => {
    const columnOptions =
      columns?.map((column) => ({
        value: column,
        label: column === DEFAULT_COLUMN ? t("labeling_config.imported_file_option") : `$${column}`,
      })) ?? [];
    if (!columns?.length) {
      columnOptions.push({ value, label: t("labeling_config.imported_file_option") });
    }
    columnOptions.push({ value: "-", label: t("labeling_config.set_manually_option") });
    return columnOptions;
  }, [columns, value, t]);

  const forName =
    template.objects > 1 ? t("labeling_config.for_object_name", { name: obj.getAttribute("name") ?? "" }) : "";

  return (
    <p>
      {t("labeling_config.use_tag_from", {
        tag: obj.tagName.toLowerCase(),
        forName,
      })}
      {columns?.length > 0 && columns[0] !== DEFAULT_COLUMN && t("labeling_config.field_prefix")}
      <Select
        triggerClassName="border"
        onChange={selectValue}
        value={isManual ? "-" : value}
        options={options}
        isInline={true}
        dataTestid={`select-trigger-use-${obj.tagName.toLowerCase().replace(/\s/g, "-")}-from-field-${isManual ? "-" : value}`}
      />
      {isManual && <Input value={newValue} onChange={handleChange} onBlur={handleBlur} onKeyDown={handleKeyDown} />}
    </p>
  );
};

const ConfigureColumns = ({ columns, template }) => {
  const { t } = useTranslation("common");
  if (!template.objects.length) return null;

  return (
    <div className={configClass.elem("object").toClassName()}>
      <h4>{t("labeling_config.configure_data_heading")}</h4>
      {template.objects.length > 1 && columns?.length > 0 && columns.length < template.objects.length && (
        <p className={configClass.elem("object-error").toClassName()}>{t("labeling_config.template_needs_more_data")}</p>
      )}
      {columns?.length === 0 && (
        <p className={configClass.elem("object-error").toClassName()}>{t("labeling_config.upload_data_required")}</p>
      )}
      {template.objects.map((obj) => (
        <ConfigureColumn key={obj.getAttribute("name")} {...{ obj, template, columns }} />
      ))}
    </div>
  );
};

const Configurator = ({
  columns,
  config,
  project,
  template,
  setTemplate,
  onBrowse,
  onSaveClick,
  onValidate,
  disableSaveButton,
  warning,
  hasChanges,
  samClassesEnabled,
  samClassesMeta,
  onSamClassesSaved,
  onSamClassesMetaChange,
}) => {
  const [configure, setConfigure] = React.useState(isEmptyConfig(config) ? "code" : "visual");
  const [visualLoaded, loadVisual] = React.useState(configure === "visual");
  const [waiting, setWaiting] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const containerRef = useRef(null);
  const [containerWidth, setContainerWidth] = useState(undefined);
  const { t } = useTranslation("common");

  const configureModes = React.useMemo(() => {
    const items = {
      code: t("labeling_config.mode_code"),
      visual: t("labeling_config.mode_visual"),
    };
    if (samClassesEnabled) {
      items.classes = t("sam_classes.mode_classes");
    }
    return items;
  }, [samClassesEnabled, t]);

  React.useEffect(() => {
    if (!samClassesEnabled && configure === "classes") {
      setConfigure("visual");
    }
  }, [configure, samClassesEnabled]);

  // Resizer hook
  const { editorWidthPixels, setEditorWidthPixels, constraints } = useConfigResizer({
    projectId: project?.id,
    containerWidth,
  });

  // Track container width for resizer constraints
  // Observes container dimensions to provide the resizer hook with container width for calculating valid min/max bounds
  useEffect(() => {
    if (!containerRef.current) return;

    let rafId;
    const updateWidth = () => {
      rafId && cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        if (containerRef.current) {
          setContainerWidth(containerRef.current.clientWidth);
        }
      });
    };

    const resizeObserver = new ResizeObserver(updateWidth);
    resizeObserver.observe(containerRef.current);

    updateWidth();

    return () => {
      rafId && cancelAnimationFrame(rafId);
      resizeObserver.disconnect();
    };
  }, []);

  // config update is debounced because of user input
  const [configToCheck, setConfigToCheck] = React.useState();
  // then we wait for validation and sample data for this config
  const [error, setError] = React.useState();
  const [parserError, setParserError] = React.useState();
  const [data, setData] = React.useState();
  const [loading, setLoading] = useState(false);
  // and only with them we'll update config in preview
  const [configToDisplay, setConfigToDisplay] = React.useState(config);
  // Track if we're in manual update mode (for large configs)
  // Once enabled, stays enabled until user clicks "Update Preview"
  const [manualUpdateMode, setManualUpdateMode] = React.useState(false);
  // Track if config has changed since last preview update
  const [hasPendingChanges, setHasPendingChanges] = React.useState(false);
  // Track the last config that was successfully validated and displayed
  const lastValidatedConfig = React.useRef(null);

  const debounceTimer = React.useRef();
  const api = useAPI();
  const isFeatureEnabled = ff.isActive(ff.FF_PREVIEW_PERFORMANCE);

  React.useEffect(() => {
    const tagCount = countConfigTags(config);
    const isLargeConfig = tagCount >= LARGE_CONFIG_TAG_THRESHOLD;
    const hasCompletedFirstValidation = lastValidatedConfig.current !== null;

    // Always validate if we haven't completed the first validation yet
    // This ensures the preview shows something on first load
    if (!hasCompletedFirstValidation) {
      // Enter manual mode for large configs, but still do the initial validation
      if (isLargeConfig && isFeatureEnabled) {
        setManualUpdateMode(true);
      }
      debounceTimer.current = window.setTimeout(() => {
        setConfigToCheck(config);
      }, 300);
      return () => window.clearTimeout(debounceTimer.current);
    }

    // After first validation: if we're in manual mode, just mark pending changes
    if (manualUpdateMode) {
      setHasPendingChanges(true);
      return;
    }

    // Enter manual mode for large configs when feature flag is enabled
    if (isLargeConfig && isFeatureEnabled) {
      setManualUpdateMode(true);
      setHasPendingChanges(true);
      return;
    }

    // Normal debounced auto-update for small configs or when feature flag is off
    debounceTimer.current = window.setTimeout(() => {
      setConfigToCheck(config);
    }, 300);

    return () => window.clearTimeout(debounceTimer.current);
  }, [config, manualUpdateMode, isFeatureEnabled]);

  // Handler for manual preview update (used for large configs)
  const handleManualUpdate = React.useCallback(() => {
    // Check if we should stay in manual mode after this update
    const tagCount = countConfigTags(config);
    const isLargeConfig = tagCount >= LARGE_CONFIG_TAG_THRESHOLD;

    // If still large, stay in manual mode but clear pending changes
    // If now small, exit manual mode
    setManualUpdateMode(isLargeConfig && isFeatureEnabled);
    setHasPendingChanges(false);
    setConfigToCheck(config);
  }, [config, isFeatureEnabled]);

  React.useEffect(() => {
    const validate = async () => {
      if (!configToCheck) return;

      setLoading(true);

      const validation = await api.callApi("validateConfig", {
        params: { pk: project.id },
        body: { label_config: configToCheck },
        errorFilter: () => true,
      });

      if (validation?.error) {
        setError(validation.response);
        setLoading(false);
        return;
      }

      setError(null);
      onValidate?.(validation);

      const sample = await api.callApi("createSampleTask", {
        params: { pk: project.id },
        body: { label_config: configToCheck },
        errorFilter: () => true,
      });

      setLoading(false);
      if (sample && !sample.error) {
        setData(sample.sample_task);
        setConfigToDisplay(configToCheck);
        // Track that we've completed a successful validation
        lastValidatedConfig.current = configToCheck;
      } else {
        // @todo validation can be done in this place,
        // @todo but for now it's extremely slow in /sample-task endpoint
        setError(sample?.response);
      }
    };
    validate();
  }, [configToCheck]);

  // code should be reloaded on every render because of uncontrolled codemirror
  // visuals should be always rendered after first render
  // so load it on the first access, then just show/hide
  const onSelect = (value) => {
    setConfigure(value);
    if (value === "visual") loadVisual(true);
  };

  const onChange = React.useCallback(
    (config) => {
      try {
        setParserError(null);
        setTemplate(config);
      } catch (e) {
        setParserError({
          detail: t("labeling_config.parser_error"),
          validation_errors: [e.message],
        });
      }
    },
    [setTemplate, t],
  );

  const onSave = async () => {
    setError(null);
    setWaiting(true);
    const res = await onSaveClick();

    setWaiting(false);

    if (res === true) {
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } else {
      setError(res);
    }
    return res;
  };

  function completeAfter(cm, pred) {
    if (!pred || pred()) {
      setTimeout(() => {
        if (!cm.state.completionActive) cm.showHint({ completeSingle: false });
      }, 100);
    }
    return CM.Pass;
  }

  function completeIfInTag(cm) {
    return completeAfter(cm, () => {
      const token = cm.getTokenAt(cm.getCursor());

      if (token.type === "string" && (!/['"]$/.test(token.string) || token.string.length === 1)) return false;

      const inner = CM.innerMode(cm.getMode(), token.state).state;

      return inner.tagName;
    });
  }

  // Memoize error to prevent AdaptivePreview re-renders when error hasn't changed
  const previewError = useMemo(
    () => parserError || error || (configure === "code" && warning) || null,
    [parserError, error, configure, warning],
  );

  const extra = (
    <p className={configClass.elem("tags-link").toClassName()}>
      <Trans
        i18nKey="labeling_config.tags_doc_line"
        components={{
          l: <a href="https://labelstud.io/tags/" target="_blank" rel="noreferrer" />,
        }}
      />
    </p>
  );

  return (
    <div className={configClass}>
      <div
        className={configClass.elem("container").toClassName()}
        ref={containerRef}
        style={{
          gridTemplateColumns: `${editorWidthPixels}px minmax(516px, 1fr)`,
        }}
      >
        <div className="flex flex-col">
          <h1>{hasChanges ? t("labeling_config.title_changed") : t("labeling_config.title")}</h1>
          <header>
            <Button
              type="button"
              data-leave={true}
              onClick={onBrowse}
              size="small"
              look="outlined"
              aria-label={t("labeling_config.browse_templates_aria")}
            >
              {t("labeling_config.browse_templates")}
            </Button>
            <ToggleItems
              items={configureModes}
              active={configure}
              onSelect={onSelect}
            />
          </header>
          {samClassesEnabled && configure !== "classes" && (
            <div className={configClass.elem("preview-info-banner").toClassName()}>
              <IconInfoOutline width={16} height={16} />
              <span>{t("sam_classes.manage_in_classes_tab")}</span>
            </div>
          )}
          <div className={configClass.elem("editor").toClassName()}>
            {configure === "classes" && samClassesEnabled && (
              <SamClassesPanel
                project={project}
                meta={samClassesMeta}
                onSaved={onSamClassesSaved}
                onMetaChange={onSamClassesMetaChange}
              />
            )}
            {configure === "code" && (
              <div className={cnm(configClass.elem("code").toClassName(), configure !== "code" ? "!hidden" : "")}>
                <CodeEditor
                  name="code"
                  id="edit_code"
                  value={config}
                  autoCloseTags={true}
                  smartIndent={true}
                  detach
                  border
                  extensions={["hint", "xml-hint"]}
                  options={{
                    mode: "xml",
                    theme: "default",
                    lineNumbers: true,
                    extraKeys: {
                      "'<'": completeAfter,
                      // "'/'": completeIfAfterLt,
                      "' '": completeIfInTag,
                      "'='": completeIfInTag,
                      "Ctrl-Space": "autocomplete",
                      "Ctrl-F": "findPersistent",
                      "Cmd-F": "findPersistent",
                    },
                    hintOptions: { schemaInfo: tags },
                  }}
                  // don't close modal with Escape while editing config
                  onKeyDown={(_editor, e) => {
                    if (e.code === "Escape") e.stopPropagation();
                  }}
                  onChange={(_editor, _data, value) => onChange(value)}
                />
              </div>
            )}
            {visualLoaded && (
              <div className={cnm(configClass.elem("visual").toClassName(), configure !== "visual" ? "!hidden" : "")}>
                {isEmptyConfig(config) && <EmptyConfigPlaceholder />}
                <ConfigureColumns columns={columns} template={template} />
                {template.controls.map((control) => (
                  <ConfigureControl control={control} template={template} key={control.getAttribute("name")} />
                ))}
                <ConfigureSettings template={template} />
              </div>
            )}
          </div>
          {disableSaveButton !== true && onSaveClick && configure !== "classes" && (
            <Form.Actions size="small" extra={configure === "code" && extra} valid>
              {saved && (
                <div className={cn("form-indicator").toClassName()}>
                  <span className={cn("form-indicator").elem("item").mod({ type: "success" }).toClassName()}>
                    {t("labeling_config.saved")}
                  </span>
                </div>
              )}
              <Button
                className="w-[120px]"
                onClick={onSave}
                waiting={waiting}
                aria-label={t("labeling_config.save_configuration_aria")}
              >
                {waiting ? t("labeling_config.saving") : t("labeling_config.save")}
              </Button>
              {isFF(FF_UNSAVED_CHANGES) && <UnsavedChanges hasChanges={hasChanges} onSave={onSave} />}
            </Form.Actions>
          )}
        </div>
        <div className="relative">
          <EditorResizer
            containerRef={containerRef}
            editorWidthPixels={editorWidthPixels}
            onResize={setEditorWidthPixels}
            constraints={constraints}
            disabled={constraints.minEditorWidth >= constraints.maxEditorWidth}
          />
          <AdaptivePreview
            config={configToDisplay}
            data={data}
            project={project}
            loading={loading}
            error={previewError}
            hasPendingUpdate={manualUpdateMode}
            onUpdatePreview={handleManualUpdate}
            isUpdating={loading}
          />
        </div>
      </div>
    </div>
  );
};

export const ConfigPage = ({
  config: initialConfig = "",
  columns: externalColumns,
  project,
  onUpdate,
  onSaveClick,
  onValidate,
  disableSaveButton,
  show = true,
  hasChanges,
  configRevision = 0,
  samClassesEnabled = false,
  samClassesMeta = null,
  onSamClassesSaved,
  onSamClassesMetaChange,
}) => {
  const [config, _setConfig] = React.useState("");
  const [mode, setMode] = React.useState("list"); // view | list
  const [selectedGroup, _setSelectedGroup] = React.useState(null);
  const [selectedRecipe, setSelectedRecipe] = React.useState(null);
  const [template, setCurrentTemplate] = React.useState(null);
  const api = useAPI();

  const setSelectedGroup = React.useCallback(
    (group) => {
      _setSelectedGroup(group);
      __lsa(`labeling_setup.list.${snakeCase(group)}`);
    },
    [_setSelectedGroup],
  );

  const setConfig = React.useCallback(
    (newConfig) => {
      _setConfig(newConfig);
      onUpdate(newConfig);
    },
    [_setConfig, onUpdate],
  );

  // setTemplate - handles both config state and Template object creation
  const setTemplate = React.useCallback(
    (newConfig) => {
      setConfig(newConfig);
      try {
        const tpl = new Template({ config: newConfig });
        tpl.onConfigUpdate = setConfig;
        setCurrentTemplate(tpl);
      } catch (e) {
        console.error("Template parsing error:", e);
      }
    },
    [setConfig, setCurrentTemplate],
  );

  const [columns, setColumns] = React.useState();

  React.useEffect(() => {
    if (externalColumns?.length) setColumns(externalColumns);
  }, [externalColumns]);

  const [warning, _setWarning] = React.useState();

  React.useEffect(() => {
    const fetchData = async () => {
      if (!externalColumns && project?.id && !columns) {
        const res = await api.callApi("dataSummary", {
          params: { pk: project.id },
          // 404 is ok, and errors here don't matter
          errorFilter: () => true,
        });

        if (res?.common_data_columns) {
          setColumns(res.common_data_columns);
        }
      }
    };
    fetchData();
  }, [project?.id, externalColumns]);

  const onSelectRecipe = React.useCallback((recipe) => {
    if (!recipe) {
      setSelectedRecipe(null);
      setMode("list");
      __lsa("labeling_setup.view.empty");
    } else {
      setTemplate(recipe.config);
      setSelectedRecipe(recipe);
      setMode("view");
      __lsa(`labeling_setup.view.${snakeCase(recipe.group)}.${snakeCase(recipe.title)}`);
    }
  });

  const onCustomTemplate = React.useCallback(() => {
    setTemplate(EMPTY_CONFIG);
    setMode("view");
    __lsa("labeling_setup.view.custom");
  });

  const onBrowse = React.useCallback(() => {
    setMode("list");
    __lsa("labeling_setup.list.browse");
  }, []);

  React.useEffect(() => {
    if (initialConfig) {
      setTemplate(initialConfig);
      setMode("view");
    }
  }, []);

  React.useEffect(() => {
    if (initialConfig) {
      setTemplate(initialConfig);
    }
  }, [initialConfig, configRevision, setTemplate]);

  if (!show) return null;

  return (
    <div className={wizardClass} data-mode="list" id="config-wizard">
      {mode === "list" && (
        <TemplatesList
          case="list"
          selectedGroup={selectedGroup}
          selectedRecipe={selectedRecipe}
          onSelectGroup={setSelectedGroup}
          onSelectRecipe={onSelectRecipe}
          onCustomTemplate={onCustomTemplate}
        />
      )}
      {mode === "view" && (
        <Configurator
          case="view"
          columns={columns}
          config={config}
          project={project}
          selectedRecipe={selectedRecipe}
          template={template}
          setTemplate={setTemplate}
          onBrowse={onBrowse}
          onValidate={onValidate}
          disableSaveButton={disableSaveButton}
          onSaveClick={onSaveClick}
          warning={warning}
          hasChanges={hasChanges}
          samClassesEnabled={samClassesEnabled}
          samClassesMeta={samClassesMeta}
          onSamClassesSaved={onSamClassesSaved}
          onSamClassesMetaChange={onSamClassesMetaChange}
        />
      )}
    </div>
  );
};
