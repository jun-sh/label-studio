import { NavLink } from "react-router-dom";
import { cn } from "../../utils/bem";
import { absoluteURL } from "../../utils/helpers";

export const MenuItem = ({
  children,
  label,
  icon,
  to,
  className,
  href,
  exact = false,
  forceReload = false,
  active = false,
  isDangerous = false,
  onClick,
  ...rest
}) => {
  const rootClass = cn("main-menu", { elem: "item" });
  const classList = [rootClass.toClassName()];

  // Route highlighting is owned by NavLink.activeClassName; only non-route items (e.g. pin) pass `active`.
  if (active) classList.push(rootClass.mod({ active: true }));

  if (isDangerous) classList.push(rootClass.mod({ dangerous: true }));

  if (className) classList.push(className);

  const linkContent = (
    <>
      {icon && <span className={rootClass.elem("item-icon").toClassName()}>{icon}</span>}
      {children ?? label}
    </>
  );

  const linkAttributes = {
    className: classList.join(" "),
    onClick,
    ...rest,
  };

  const activeClassName = rootClass.mod({ active: true }).toClassName();
  const finalHref = to ?? href;

  if (forceReload) {
    const reloadTarget = to ?? href;
    linkAttributes.onClick = (event) => {
      event?.preventDefault();
      if (reloadTarget) {
        window.location.replace(reloadTarget);
      }
    };
  }

  return (
    <li>
      {to ? (
        <NavLink to={finalHref} {...linkAttributes} exact={exact} activeClassName={activeClassName} data-external>
          {linkContent}
        </NavLink>
      ) : finalHref ? (
        <a href={absoluteURL(finalHref)} {...linkAttributes}>
          {linkContent}
        </a>
      ) : (
        <span {...linkAttributes}>{linkContent}</span>
      )}
    </li>
  );
};
