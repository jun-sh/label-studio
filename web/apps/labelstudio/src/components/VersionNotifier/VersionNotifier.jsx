import { createContext } from "react";

/** Data Lab: upstream LS update prompts are disabled; keep exports for compatibility. */
const VersionContext = createContext();

export const VersionProvider = ({ children }) => {
  return <VersionContext.Provider value={undefined}>{children}</VersionContext.Provider>;
};

export const VersionNotifier = () => null;
