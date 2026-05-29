import type { TipsCollection } from "./types";

/** Marketing tips disabled for internal deployment; live `/heidi-tips` also returns empty payloads. */
export const defaultTipsCollection: TipsCollection = {
  projectCreation: [],
  organizationPage: [],
  projectSettings: [],
};
