import { Menubar } from "../components/Menubar/Menubar";
import { ProjectRoutes } from "../routes/ProjectRoutes";
import { useOrgValidation } from "../hooks/useOrgValidation";

export const RootPage = ({ content }) => {
  useOrgValidation();
  const pinned = localStorage.getItem("sidebar-pinned") === "true";
  const opened = pinned && localStorage.getItem("sidebar-opened") === "true";

  return (
    <Menubar
      enabled={true}
      defaultOpened={opened}
      defaultPinned={pinned}
      onSidebarToggle={(visible) => {
        localStorage.setItem("sidebar-opened", visible);
        window.dispatchEvent(new Event("datalab:layout"));
      }}
      onSidebarPin={(pinned) => {
        localStorage.setItem("sidebar-pinned", pinned);
        window.dispatchEvent(new Event("datalab:layout"));
      }}
    >
      <ProjectRoutes content={content} />
    </Menubar>
  );
};
