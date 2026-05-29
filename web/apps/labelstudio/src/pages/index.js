import { ProjectsPage } from "./Projects/Projects";
import { HomePage } from "./Home/HomePage";
import { DataVizPage } from "./DataViz";
import { CollectionPage } from "./Collection";
import { OrganizationPage } from "./Organization";
import { ModelsPage } from "./Organization/Models/ModelsPage";
import { FF_HOMEPAGE, isFF } from "../utils/feature-flags";
import { pages } from "@humansignal/app-common";

export const Pages = [
  isFF(FF_HOMEPAGE) && HomePage,
  DataVizPage,
  CollectionPage,
  ProjectsPage,
  OrganizationPage,
  ModelsPage,
  pages.AccountSettingsPage,
].filter(Boolean);
