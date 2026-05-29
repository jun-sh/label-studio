import { EnterpriseBadge, IconSpark } from "@humansignal/ui";
import { Alert, AlertTitle, AlertDescription } from "@humansignal/shad/components/ui/alert";
import { IconCloudProviderAzure } from "@humansignal/icons";
import type { ProviderConfig } from "@humansignal/app-common/blocks/StorageProviderForm/types/provider";

const azureSpiProvider: ProviderConfig = {
  name: "azure_spi",
  title: "Azure Blob Storage\nwith Service Principal",
  description:
    "Configure your Azure Blob Storage connection using Service Principal authentication for enhanced security (proxy only)",
  icon: IconCloudProviderAzure,
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

export default azureSpiProvider;
