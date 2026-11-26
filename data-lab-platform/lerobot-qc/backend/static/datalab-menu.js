/*
 * Datalab main menu for QC pages: the header's logo area opens a floating sidebar with the
 * same entries, order and look as the Datalab (Label Studio shell) main menu.
 * Needs i18n.js (window.LA_I18N) loaded first.
 */
(function () {
  "use strict";

  // Same entries and order as the Datalab main menu; icons are copies of web/libs/ui icons.
  const ITEMS = [
    { key: "menu_home", href: "/", icon: "home" },
    { key: "menu_data", href: "/data", icon: "layers2" },
    { key: "menu_collection", href: "/collection", icon: "upload" },
    { key: "menu_qc", href: "/qc/", icon: "clipboard-check", current: true },
    { key: "menu_projects", href: "/projects", icon: "edit" },
    { key: "menu_organization", href: "/organization", icon: "people" },
  ];

  const t = (key) => window.LA_I18N.t(key);

  function buildMenu() {
    const nav = document.createElement("nav");
    nav.className = "dl-menu";
    nav.id = "dlMainMenu";
    nav.hidden = true;
    const list = document.createElement("ul");
    list.className = "dl-menu__list";
    for (const item of ITEMS) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.className = "dl-menu__item";
      link.href = item.href;
      if (item.current) {
        link.classList.add("is-active");
        link.setAttribute("aria-current", "page");
      }
      const icon = document.createElement("span");
      icon.className = `dl-menu__icon dl-menu__icon--${item.icon}`;
      icon.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.dataset.dlI18n = item.key;
      label.textContent = t(item.key);
      link.append(icon, label);
      li.append(link);
      list.append(li);
    }
    nav.append(list);
    return nav;
  }

  function init() {
    const trigger = document.getElementById("dlMenuTrigger");
    if (!trigger || document.getElementById("dlMainMenu")) return;
    const menu = buildMenu();
    trigger.after(menu);
    trigger.setAttribute("aria-controls", menu.id);

    const setOpen = (open) => {
      menu.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
      trigger.classList.toggle("is-open", open);
    };
    setOpen(false);

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      setOpen(menu.hidden);
    });
    // Clicking the dimmed page or pressing Escape closes the menu, as in Datalab.
    document.addEventListener("click", (event) => {
      if (!menu.hidden && !menu.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hidden) {
        setOpen(false);
        trigger.focus();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
