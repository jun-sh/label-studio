import { EnterpriseBadge, IconSpark } from "@humansignal/ui";
import { Alert, AlertTitle, AlertDescription } from "@humansignal/shad/components/ui/alert";
import { IconCloudProviderDatabricks } from "@humansignal/icons";
import type { ProviderConfig } from "@humansignal/app-common/blocks/StorageProviderForm/types/provider";

const databricksProvider: ProviderConfig = {
  name: "databricks",
  title: "Databricks Files\n(UC Volumes)",
  description: "Configure your Databricks Unity Catalog Volumes connection with all required settings (proxy only)",
  icon: IconCloudProviderDatabricks,
  disabled: true,
  badge: <EnterpriseBadge />,
  fields: [
    {
      name: "enterprise_info",
      type: "message",
      content: (
        <Alert variant="gradient">
          <IconSpark />
          <AlertTitle>Not enabled</AlertTitle>
          <AlertDescription>This storage option is not enabled in this deployment.</AlertDescription>
        </Alert>
      ),
    },
  ],
  layout: [{ fields: ["enterprise_info"] }],
};

export default databricksProvider;
