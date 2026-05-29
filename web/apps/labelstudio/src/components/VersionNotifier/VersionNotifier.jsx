import { format } from "date-fns";
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useAPI } from "../../providers/ApiProvider";
import { getDateFnsLocale } from "../../utils/dateFnsLocale";
import { cn } from "../../utils/bem";
import "./VersionNotifier.scss";
import { IconBell } from "@humansignal/icons";

/** Shown in sidebar /version link; upstream Label Studio API still reports its own semver. */
const DISPLAY_PRODUCT_VERSION = "1.0.0";

const VersionContext = createContext();

export const VersionProvider = ({ children }) => {
  const api = useAPI();

  const [state, dispatch] = useReducer((state, action) => {
    if (action.type === "fetch-version") {
      return { ...state, ...action.payload };
    }
  });

  const fetchVersion = useCallback(async () => {
    const response = await api.callApi("version");

    if (response !== null) {
      const data = response["label-studio-os-package"];

      dispatch({
        type: "fetch-version",
        payload: {
          version: data.version,
          latestVersion: data.latest_version_from_pypi,
          newVersion: data.current_version_is_outdated,
          latestVersionUploadTime: data.latest_version_upload_time,
        },
      });
    }
  }, []);

  useEffect(() => {
    fetchVersion();
  }, []);

  return <VersionContext.Provider value={state}>{children}</VersionContext.Provider>;
};

export const VersionNotifier = ({ showNewVersion, showCurrentVersion }) => {
  const { t, i18n } = useTranslation("common");
  const dateLocale = useMemo(() => getDateFnsLocale(i18n.language), [i18n.language]);
  const { newVersion, latestVersionUploadTime, latestVersion, version } = useContext(VersionContext) ?? {};
  const url = `https://labelstud.io/redirect/update?version=${version}`;

  const updateTimeFormatted = useMemo(() => {
    if (!latestVersionUploadTime) return undefined;
    try {
      return format(new Date(latestVersionUploadTime), "MMM d", { locale: dateLocale });
    } catch {
      return undefined;
    }
  }, [latestVersionUploadTime, dateLocale]);

  return newVersion && showNewVersion ? (
    <li className={cn("version-notifier").toClassName()}>
      <a href={url} target="_blank" rel="noreferrer">
        <div className={cn("version-notifier").elem("icon").toClassName()}>
          <IconBell />
        </div>
        <div className={cn("version-notifier").elem("content").toClassName()}>
          <div className={cn("version-notifier").elem("title").toClassName()} data-date={updateTimeFormatted}>
            {t("shell.version.new_available", { version: latestVersion })}
          </div>
          <div className={cn("version-notifier").elem("description").toClassName()}>
            {t("shell.version.current", { version })}
          </div>
        </div>
      </a>
    </li>
  ) : version && showCurrentVersion ? (
    <Link className={cn("current-version").toClassName()} to="/version" target="_blank">
      v{DISPLAY_PRODUCT_VERSION}
    </Link>
  ) : null;
};
