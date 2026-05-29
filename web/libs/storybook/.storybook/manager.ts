import { addons } from "storybook/manager-api";
import { create } from "storybook/theming/create";

const theme = create({
  base: "dark",
  brandTitle: "Data Lab",
  brandUrl: "/",
  brandImage: "logo.svg",
  brandTarget: "_self",
});

addons.setConfig({
  theme,
});
