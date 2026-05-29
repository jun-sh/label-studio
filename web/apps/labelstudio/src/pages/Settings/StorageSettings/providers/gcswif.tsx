import { EnterpriseBadge, IconSpark } from "@humansignal/ui";
import { Alert, AlertTitle, AlertDescription } from "@humansignal/shad/components/ui/alert";
import { IconCloudProviderGCS } from "@humansignal/icons";
import type { ProviderConfig } from "@humansignal/app-common/blocks/StorageProviderForm/types/provider";

const gcsWifProvider: ProviderConfig = {
  name: "gcswif",
  title: "Google Cloud Storage\n(WIF Auth)",
  description:
    "Configure your Google Cloud Storage connection with Workload Identity Federation authentication (proxy only)",
  icon: IconCloudProviderGCS,
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

export default gcsWifProvider;
