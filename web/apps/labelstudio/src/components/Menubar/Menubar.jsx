import { createContext, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { StaticContent } from "../../app/StaticContent/StaticContent";
import {
  IconBook,
  IconFolder,
  IconHome,
  IconHotkeys,
  IconPeople,
  IconPersonInCircle,
  IconPin,
  IconTerminal,
  IconDoor,
} from "@humansignal/icons";
import { LSLogo } from "../../assets/images";
import { Button, Userpic, ThemeToggle } from "@humansignal/ui";
import { useConfig } from "../../providers/ConfigProvider";
import { useContextComponent, useFixedLocation } from "../../providers/RoutesProvider";
import { useAuth } from "@humansignal/core/providers/AuthProvider";
import { cn } from "../../utils/bem";
import { absoluteURL, isDefined } from "../../utils/helpers";
import { Breadcrumbs } from "../Breadcrumbs/Breadcrumbs";
import { Dropdown } from "@humansignal/ui";
import { Hamburger } from "../Hamburger/Hamburger";
import { Menu } from "../Menu/Menu";
import { VersionNotifier, VersionProvider } from "../VersionNotifier/VersionNotifier";
import "./Menubar.scss";
import "./MenuContent.scss";
import "./MenuSidebar.scss";
import { FF_HOMEPAGE } from "../../utils/feature-flags";
import { pages } from "@humansignal/app-common";
import { isFF } from "../../utils/feature-flags";
import { ff } from "@humansignal/core";
import { openHotkeyHelp } from "@humansignal/app-common/pages/AccountSettings/sections/Hotkeys/Help";
import { getDataLabLinks } from "../../config/dataLabLinks";

export const MenubarContext = createContext();

const LeftContextMenu = ({ className }) => (
  <StaticContent id="context-menu-left" className={className}>
    {(template) => <Breadcrumbs fromTemplate={template} />}
  </StaticContent>
);

const RightContextMenu = ({ className, ...props }) => {
  const { ContextComponent, contextProps } = useContextComponent();

  return ContextComponent ? (
    <div className={className}>
      <ContextComponent {...props} {...(contextProps ?? {})} />
    </div>
  ) : (
    <StaticContent id="context-menu-right" className={className} />
  );
};

export const Menubar = ({ enabled, defaultOpened, defaultPinned, children, onSidebarToggle, onSidebarPin }) => {
  const { t } = useTranslation("common");
  const menuDropdownRef = useRef();
  const useMenuRef = useRef();
  const { user, isLoading } = useAuth();
  const location = useFixedLocation();

  const config = useConfig();
  const dataLabLinks = useMemo(() => getDataLabLinks(), []);
  const isCollectionViz =
    location.pathname.replace(/\/$/, "") === "/collection" &&
    Boolean(new URLSearchParams(location.search).get("station"));

  const [sidebarOpened, setSidebarOpened] = useState(defaultOpened ?? false);
  const [sidebarPinned, setSidebarPinned] = useState(defaultPinned ?? false);
  const [PageContext, setPageContext] = useState({
    Component: null,
    props: {},
  });

  const menubarClass = cn("menu-header");
  const menubarContext = menubarClass.elem("context");
  const sidebarClass = cn("sidebar");
  const contentClass = cn("content-wrapper");
  const contextItem = menubarClass.elem("context-item");
  const showNewsletterDot = !isDefined(user?.allow_newsletters);

  const sidebarPin = useCallback(
    (e) => {
      e.preventDefault();

      const newState = !sidebarPinned;

      setSidebarPinned(newState);
      onSidebarPin?.(newState);
    },
    [sidebarPinned],
  );

  const sidebarToggle = useCallback(
    (visible) => {
      const newState = visible;

      setSidebarOpened(newState);
      onSidebarToggle?.(newState);
    },
    [sidebarOpened],
  );

  const providerValue = useMemo(
    () => ({
      PageContext,

      setContext(ctx) {
        setTimeout(() => {
          setPageContext({
            ...PageContext,
            Component: ctx,
          });
        });
      },

      setProps(props) {
        setTimeout(() => {
          setPageContext({
            ...PageContext,
            props,
          });
        });
      },

      contextIsSet(ctx) {
        return PageContext.Component === ctx;
      },
    }),
    [PageContext],
  );

  useEffect(() => {
    if (!sidebarPinned) {
      menuDropdownRef?.current?.close();
    }
    useMenuRef?.current?.close();
  }, [location]);

  useEffect(() => {
    window.dispatchEvent(new Event("resize"));
  }, [sidebarPinned, sidebarOpened]);

  return (
    <div className={contentClass}>
      {enabled && (
        <div className={menubarClass}>
          <Dropdown.Trigger dropdown={menuDropdownRef} closeOnClickOutside={!sidebarPinned}>
            <div className={`${menubarClass.elem("trigger")} main-menu-trigger`}>
              <LSLogo className={`${menubarClass.elem("logo")}`} alt={t("shell.menubar.logo_alt")} />
              <Hamburger opened={sidebarOpened} />
            </div>
          </Dropdown.Trigger>

          <div className={menubarContext}>
            <LeftContextMenu className={contextItem.mod({ left: true }).toClassName()} />
            <RightContextMenu className={contextItem.mod({ right: true }).toClassName()} />
          </div>

          <div className={menubarClass.elem("hotkeys").toClassName()}>
            <div className={menubarClass.elem("hotkeys-button").toClassName()}>
              <Button
                variant="neutral"
                look="outlined"
                tooltip={t("shell.menubar.hotkeys_tooltip")}
                data-testid="hotkeys-button"
                size="small"
                onClick={() => {
                  openHotkeyHelp([
                    "annotation",
                    "data_manager",
                    "regions",
                    "tools",
                    "audio",
                    "video",
                    "timeseries",
                    "image_gallery",
                  ]);
                }}
                icon={<IconHotkeys />}
              />
            </div>
          </div>

          {ff.isActive(ff.FF_THEME_TOGGLE) && (
            <ThemeToggle
              labels={{
                Auto: t("shell.theme.auto"),
                Light: t("shell.theme.light"),
                Dark: t("shell.theme.dark"),
              }}
            />
          )}

          <Dropdown.Trigger
            ref={useMenuRef}
            align="right"
            content={
              <Menu>
                <Menu.Item
                  icon={<IconPersonInCircle />}
                  label={t("shell.menubar.account_settings")}
                  href={pages.AccountSettingsPage.path}
                />
                {/* <Menu.Item label="Dark Mode"/> */}
                <Menu.Item icon={<IconDoor />} label={t("shell.menubar.logout")} href={absoluteURL("/logout")} data-external />
                {showNewsletterDot && (
                  <>
                    <Menu.Divider />
                    <Menu.Item
                      className={cn("newsletter-menu-item").toClassName()}
                      href={pages.AccountSettingsPage.path}
                    >
                      <span>{t("shell.menubar.newsletter_notice")}</span>
                      <span className={cn("newsletter-menu-badge").toClassName()} />
                    </Menu.Item>
                  </>
                )}
              </Menu>
            }
          >
            <div title={user?.email} className={menubarClass.elem("user").toClassName()}>
              <Userpic user={user} isInProgress={isLoading} />
              {showNewsletterDot && <div className={menubarClass.elem("userpic-badge").toClassName()} />}
            </div>
          </Dropdown.Trigger>
        </div>
      )}

      <VersionProvider>
        <div className={contentClass.elem("body").toClassName()}>
          {enabled && (
            <Dropdown
              ref={menuDropdownRef}
              onToggle={sidebarToggle}
              onVisibilityChanged={() => window.dispatchEvent(new Event("resize"))}
              visible={sidebarOpened}
              className={[sidebarClass, sidebarClass.mod({ floating: !sidebarPinned })].join(" ")}
              style={{ width: 240 }}
            >
              <Menu>
                {isFF(FF_HOMEPAGE) && (
                  <Menu.Item
                    label={
                      isCollectionViz ? t("shell.menubar.back_to_collection_list") : t("shell.menubar.home")
                    }
                    to={isCollectionViz ? undefined : "/"}
                    href={isCollectionViz ? "/collection" : undefined}
                    title={
                      isCollectionViz ? t("shell.menubar.back_to_collection_list") : t("shell.menubar.home")
                    }
                    icon={<IconHome />}
                    data-external
                    exact={!isCollectionViz}
                    forceReload={isCollectionViz}
                  />
                )}
                <Menu.Item
                  label={t("shell.menubar.projects")}
                  to="/projects"
                  icon={<IconFolder />}
                  data-external
                  exact
                />
                <Menu.Item
                  label={t("shell.menubar.organization")}
                  to="/organization"
                  icon={<IconPeople />}
                  data-external
                  exact
                />

                <Menu.Spacer />

                <VersionNotifier showNewVersion />

                {dataLabLinks.apiDocs ? (
                  <Menu.Item
                    label={t("shell.menubar.api")}
                    href={dataLabLinks.apiDocs}
                    icon={<IconTerminal />}
                    target="_blank"
                  />
                ) : null}
                {dataLabLinks.documentation ? (
                  <Menu.Item
                    label={t("shell.menubar.docs")}
                    href={dataLabLinks.documentation}
                    icon={<IconBook />}
                    target="_blank"
                  />
                ) : null}

                <VersionNotifier showCurrentVersion />

                <Menu.Divider />

                <Menu.Item
                  icon={<IconPin />}
                  className={sidebarClass.elem("pin").toClassName()}
                  onClick={sidebarPin}
                  active={sidebarPinned}
                >
                  {sidebarPinned ? t("shell.menubar.unpin_menu") : t("shell.menubar.pin_menu")}
                </Menu.Item>
              </Menu>
            </Dropdown>
          )}

          <MenubarContext.Provider value={providerValue}>
            <div
              className={contentClass
                .elem("content")
                .mod({ withSidebar: sidebarPinned && sidebarOpened })
                .toClassName()}
            >
              {children}
            </div>
          </MenubarContext.Provider>
        </div>
      </VersionProvider>
    </div>
  );
};
