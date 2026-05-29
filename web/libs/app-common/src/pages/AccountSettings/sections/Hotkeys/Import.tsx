import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@humansignal/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@humansignal/shad/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@humansignal/shad/components/ui/alert";

// Type definitions
interface Hotkey {
  id: string;
  section: string;
  element: string;
  label: string;
  key: string;
  mac?: string;
  active: boolean;
  subgroup?: string;
  description?: string;
}

interface ImportData {
  hotkeys?: Hotkey[];
  settings?: {
    autoTranslatePlatforms?: boolean;
  };
}

interface ImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImport: (data: ImportData | Hotkey[]) => void | Promise<void>;
}

/**
 * ImportDialog - A dialog component for importing hotkey configurations
 *
 * This component allows users to import hotkey configurations by pasting JSON data.
 * It validates the imported data structure and provides error feedback.
 *
 * @param {ImportDialogProps} props - The component props
 * @returns {React.ReactElement} The ImportDialog component
 */
export const ImportDialog = ({ open, onOpenChange, onImport }: ImportDialogProps) => {
  const { t } = useTranslation("common");
  const [importText, setImportText] = useState<string>("");
  const [error, setError] = useState<string>("");

  /**
   * Validates a single hotkey object structure
   * @param {unknown} hotkey - The hotkey object to validate
   * @throws {Error} If the hotkey is missing required fields
   */
  const validateHotkey = (hotkey: unknown): void => {
    if (!hotkey || typeof hotkey !== "object") {
      throw new Error(t("hotkeys.import.invalid_hotkey_object"));
    }

    const hotkeyObj = hotkey as Record<string, unknown>;
    const requiredFields = ["id", "section", "element", "label", "key"];
    const missingFields = requiredFields.filter((field) => !hotkeyObj[field]);

    if (missingFields.length > 0) {
      throw new Error(t("hotkeys.import.missing_fields", { fields: missingFields.join(", ") }));
    }
  };

  /**
   * Handles the import process
   * Parses JSON, validates structure, and calls the onImport callback
   */
  const handleImport = (): void => {
    try {
      setError("");

      if (!importText.trim()) {
        throw new Error(t("hotkeys.import.error_enter_json"));
      }

      const parsedData: unknown = JSON.parse(importText);

      let hotkeys: unknown[];

      if (Array.isArray(parsedData)) {
        hotkeys = parsedData;
      } else if (parsedData && typeof parsedData === "object" && "hotkeys" in parsedData) {
        const dataObj = parsedData as { hotkeys?: unknown };
        if (!Array.isArray(dataObj.hotkeys)) {
          throw new Error(t("hotkeys.import.invalid_hotkeys_array"));
        }
        hotkeys = dataObj.hotkeys;
      } else {
        throw new Error(t("hotkeys.import.invalid_format"));
      }

      if (hotkeys.length === 0) {
        throw new Error(t("hotkeys.import.no_hotkeys"));
      }

      hotkeys.forEach((hotkey: unknown, index: number) => {
        try {
          validateHotkey(hotkey);
        } catch (validationError: unknown) {
          const errorMessage =
            validationError instanceof Error ? validationError.message : t("hotkeys.errors.unknown");
          throw new Error(t("hotkeys.import.hotkey_index_error", { index, message: errorMessage }));
        }
      });

      onImport(parsedData as ImportData | Hotkey[]);

      resetDialogState();
    } catch (err: unknown) {
      if (err instanceof SyntaxError) {
        setError(t("hotkeys.import.json_parse_error"));
        return;
      }
      const errorMessage = err instanceof Error ? err.message : t("hotkeys.errors.unknown");
      setError(errorMessage);
    }
  };

  const resetDialogState = (): void => {
    setImportText("");
    setError("");
    onOpenChange(false);
  };

  const handleCancel = (): void => {
    resetDialogState();
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>): void => {
    setImportText(e.target.value);
    if (error) {
      setError("");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[525px] bg-neutral-surface">
        <DialogHeader>
          <DialogTitle>{t("hotkeys.import.title")}</DialogTitle>
          <DialogDescription>{t("hotkeys.import.description")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <label
            htmlFor="import-json"
            className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
          >
            {t("hotkeys.import.json_label")}
          </label>
          <textarea
            id="import-json"
            className="flex min-h-[150px] w-full rounded-md border border-neutral-border bg-transparent px-tight py-tighter typography-body-small placeholder:text-neutral-content-subtler focus-visible:ring-4 focus-visible:ring-primary-focus-outline focus-visible:border-neutral-border-bolder focus-visible:outline-0 transition-all resize-none"
            placeholder={t("hotkeys.import.placeholder")}
            value={importText}
            onChange={handleTextareaChange}
            aria-describedby={error ? "import-error" : undefined}
          />

          {error && (
            <Alert variant="destructive" id="import-error">
              <AlertTitle>{t("hotkeys.import.error_title")}</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter>
          <Button variant="neutral" onClick={handleCancel}>
            {t("hotkeys.ui.cancel")}
          </Button>
          <Button onClick={handleImport} disabled={!importText.trim()}>
            {t("hotkeys.import.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
