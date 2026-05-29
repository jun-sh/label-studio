import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@humansignal/ui";
import { LeaveBlocker, type LeaveBlockerCallbacks } from "../../../components/LeaveBlocker/LeaveBlocker";
import { modal } from "../../../components/Modal/Modal";
import { Space } from "../../../components/Space/Space";

type SaveAndLeaveButtonProps = {
  onSave: () => Promise<void>;
  text?: string;
  ariaLabel?: string;
};
const SaveAndLeaveButton = ({ onSave, text = "Save and Leave", ariaLabel = "Save changes" }: SaveAndLeaveButtonProps) => {
  const [saving, setSaving] = useState(false);
  const saveHandler = useCallback(async () => {
    setSaving(true);
    await onSave();
    setSaving(false);
  }, [onSave]);
  return (
    <Button size="small" onClick={saveHandler} waiting={saving} aria-label={ariaLabel}>
      {text}
    </Button>
  );
};

type UnsavedChangesModalProps = {
  onSave: () => void;
  onCancel?: () => void;
  onDiscard?: () => void;
  cancelText?: string;
  discardText?: string;
  okText?: string;
  okAriaLabel?: string;
  title?: string;
  body?: string;
};

export const unsavedChangesModal = ({
  onSave,
  onCancel,
  onDiscard,
  cancelText,
  discardText,
  okText,
  okAriaLabel,
  title = "You have unsaved changes.",
  body = "Would you like to save them before leaving?",
  ...props
}: UnsavedChangesModalProps) => {
  let modalInstance: any;
  const saveAndLeave = async () => {
    await onSave?.();
    modalInstance?.close();
  };
  modalInstance = modal({
    ...props,
    title,
    body: () => <>{body}</>,
    allowClose: true,
    footer: (
      <Space align="end">
        <Button
          look="outlined"
          size="small"
          onClick={() => {
            onCancel?.();
            modalInstance?.close();
          }}
          autoFocus
        >
          {cancelText ?? "Cancel"}
        </Button>

        {onDiscard && (
          <Button
            variant="negative"
            look="outlined"
            onClick={() => {
              onDiscard?.();
              modalInstance?.close();
            }}
            size="small"
          >
            {discardText ?? "Discard and leave"}
          </Button>
        )}

        <SaveAndLeaveButton onSave={saveAndLeave} text={okText} ariaLabel={okAriaLabel} />
      </Space>
    ),
    style: { width: 512 },
    unique: "UNSAVED_CHANGES_MODAL",
  });
};

type UnsavedChangesProps = {
  hasChanges: boolean;
  onSave: () => any;
};

/**
 * Component that blocks navigation if there are unsaved changes
 * @param hasChanges - flag that indicates if there are unsaved changes
 * @param onSave - function that should be called to save changes
 */
export const UnsavedChanges = ({ hasChanges, onSave }: UnsavedChangesProps) => {
  const { t } = useTranslation("common");
  const saveHandlerRef = useRef(onSave);
  saveHandlerRef.current = onSave;
  const blockHandler = useCallback(async ({ continueCallback, cancelCallback }: LeaveBlockerCallbacks) => {
    const wrappedOnSave = async () => {
      const result = await saveHandlerRef.current?.();
      if (result === true) {
        continueCallback && setTimeout(continueCallback, 0);
      } else {
        // We consider that user tries to save changes, but as long as there are some errors,
        // we just close the modal to allow user to see and fix them
        cancelCallback?.();
      }
    };

    unsavedChangesModal({
      onSave: wrappedOnSave,
      onCancel: cancelCallback,
      onDiscard: continueCallback,
      title: t("labeling_config.unsaved_title"),
      body: t("labeling_config.unsaved_body"),
      cancelText: t("labeling_config.unsaved_cancel"),
      discardText: t("labeling_config.unsaved_discard"),
      okText: t("labeling_config.unsaved_save_leave"),
      okAriaLabel: t("labeling_config.unsaved_save_aria"),
    });
  }, [t]);

  return <LeaveBlocker active={hasChanges} onBlock={blockHandler} />;
};
