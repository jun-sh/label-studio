import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, cnm } from "@humansignal/ui";
import { IconInfoOutline, IconTrash } from "@humansignal/icons";
import { Form, Input } from "../../components/Form";
import { useAPI } from "../../providers/ApiProvider";
import { cn } from "../../utils/bem";
import { Palette } from "../../utils/colors";
import { colorNames } from "../CreateProject/Config/colors";
import { unsavedChangesModal } from "../CreateProject/Config/UnsavedChanges";

const configClass = cn("configure");

const cloneClasses = (classes = []) => classes.map((item) => ({ ...item }));

const willEssentialClassDataChange = (initialClasses, nextClasses) => {
  const nextValues = new Set(nextClasses.map((item) => item.value));
  return initialClasses.some((item) => !nextValues.has(item.value));
};

export const SamClassesPanel = ({ project, meta, onSaved, onMetaChange }) => {
  const { t } = useTranslation("common");
  const api = useAPI();
  const palette = useMemo(() => Palette(), []);
  const refLabels = useRef();
  const [classes, setClasses] = useState(() => cloneClasses(meta?.classes));
  const [baseline, setBaseline] = useState(() => cloneClasses(meta?.classes));
  const [waiting, setWaiting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const next = cloneClasses(meta?.classes);
    setClasses(next);
    setBaseline(next);
  }, [meta?.classes]);

  const hasChanges = useMemo(() => JSON.stringify(classes) !== JSON.stringify(baseline), [classes, baseline]);

  const onAddLabels = useCallback(() => {
    const raw = refLabels.current?.value ?? "";
    const names = raw
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!names.length) return;

    setClasses((prev) => {
      const existing = new Set(prev.map((item) => item.value));
      const added = [...prev];
      names.forEach((value) => {
        if (existing.has(value)) return;
        existing.add(value);
        added.push({ value, background: palette.next().value });
      });
      return added;
    });
    refLabels.current.value = "";
  }, [palette]);

  const onKeyPress = (e) => {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      onAddLabels();
    }
  };

  const onRemove = (value) => {
    setClasses((prev) => prev.filter((item) => item.value !== value));
  };

  const onColorChange = (value, background) => {
    setClasses((prev) => prev.map((item) => (item.value === value ? { ...item, background } : item)));
  };

  const performSave = useCallback(async () => {
    setError(null);
    setWaiting(true);

    const res = await api.callApi("updateSamClasses", {
      params: { pk: project.id },
      body: { classes },
      errorFilter: () => true,
    });

    setWaiting(false);

    if (!res || res.error) {
      setError(res?.response ?? res?.error);
      return false;
    }

    const nextClasses = cloneClasses(res.classes);
    setClasses(nextClasses);
    setBaseline(nextClasses);
    onMetaChange?.(res);
    await onSaved?.(res);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    return true;
  }, [api, classes, onMetaChange, onSaved, project.id]);

  const onSave = useCallback(async () => {
    if (!hasChanges) return true;

    if (willEssentialClassDataChange(baseline, classes)) {
      return new Promise((resolve) => {
        unsavedChangesModal({
          title: t("sam_classes.essential_title"),
          body: t("sam_classes.essential_body"),
          cancelText: t("labeling_config.unsaved_cancel"),
          okText: t("sam_classes.essential_confirm"),
          okAriaLabel: t("sam_classes.essential_confirm"),
          onCancel: () => resolve(false),
          onSave: async () => {
            const ok = await performSave();
            resolve(ok);
          },
        });
      });
    }

    return performSave();
  }, [baseline, classes, hasChanges, performSave, t]);

  return (
    <div className={configClass.elem("sam-classes").toClassName()}>
      {meta?.in_sync === false && (
        <div className={configClass.elem("preview-info-banner").toClassName()}>
          <IconInfoOutline width={16} height={16} />
          <span>{t("sam_classes.out_of_sync_banner")}</span>
        </div>
      )}

      <div className={configClass.elem("labels").toClassName()}>
        <form className={configClass.elem("add-labels").toClassName()} action="">
          <h4>{t("labeling_config.add_label_names_heading")}</h4>
          <span>{t("labeling_config.add_labels_hint")}</span>
          <textarea
            name="labels"
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
          <h3>{t("labeling_config.labels_with_count", { count: classes.length })}</h3>
          <ul>
            {classes.map((label) => (
              <li key={label.value} className={cnm(configClass.elem("label").toClassName(), "group")}>
                <span className={cnm(configClass.elem("label-text").toClassName(), "flex")}>
                  <label style={{ background: label.background }}>
                    <Input
                      type="color"
                      className={configClass.elem("label-color").toClassName()}
                      value={colorNames[label.background] || label.background}
                      onChange={(e) => onColorChange(label.value, e.target.value)}
                    />
                  </label>
                  <span>{label.value}</span>
                </span>
                <Button
                  type="button"
                  look="string"
                  size="smaller"
                  variant="negative"
                  onClick={() => onRemove(label.value)}
                  aria-label={t("labeling_config.delete_label_aria")}
                  className="hidden !p-0 z-10 absolute right-0 [&_span]:!p-0 group-hover:inline-flex"
                  leading={<IconTrash className="w-4 h-4 fill-[currentColor]" />}
                />
              </li>
            ))}
          </ul>
        </div>
      </div>

      <Form.Actions size="small" valid>
        {saved && (
          <div className={cn("form-indicator").toClassName()}>
            <span className={cn("form-indicator").elem("item").mod({ type: "success" }).toClassName()}>
              {t("labeling_config.saved")}
            </span>
          </div>
        )}
        {error && (
          <div className={cn("form-indicator").toClassName()}>
            <span className={cn("form-indicator").elem("item").mod({ type: "error" }).toClassName()}>
              {error?.detail || error?.message || t("sam_classes.save_error")}
            </span>
          </div>
        )}
        <Button className="w-[120px]" onClick={onSave} waiting={waiting} disabled={!hasChanges} aria-label={t("sam_classes.save_aria")}>
          {waiting ? t("labeling_config.saving") : t("labeling_config.save")}
        </Button>
      </Form.Actions>
    </div>
  );
};
