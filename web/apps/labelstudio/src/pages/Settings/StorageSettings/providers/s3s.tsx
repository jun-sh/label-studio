import { EnterpriseBadge, IconSpark } from "@humansignal/ui";
import { Alert, AlertTitle, AlertDescription } from "@humansignal/shad/components/ui/alert";
import { IconCloudProviderS3 } from "@humansignal/icons";
import type { ProviderConfig } from "@humansignal/app-common/blocks/StorageProviderForm/types/provider";

const s3sProvider: ProviderConfig = {
  name: "s3s",
  title: "Amazon S3\nwith IAM Role",
  description: "Configure your AWS S3 connection using IAM role access for enhanced security (proxy only)",
  icon: IconCloudProviderS3,
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

export default s3sProvider;
