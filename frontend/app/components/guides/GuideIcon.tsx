/** The icon a guide names in its JSON, as an MUI icon. */
import CloudIcon from "@mui/icons-material/Cloud";
import DnsIcon from "@mui/icons-material/Dns";
import ExploreIcon from "@mui/icons-material/Explore";
import HubIcon from "@mui/icons-material/Hub";
import KeyIcon from "@mui/icons-material/Key";
import PersonAddIcon from "@mui/icons-material/PersonAdd";
import SchoolIcon from "@mui/icons-material/School";
import StorefrontIcon from "@mui/icons-material/Storefront";

const ICONS = {
  Cloud: CloudIcon,
  Dns: DnsIcon,
  Explore: ExploreIcon,
  Hub: HubIcon,
  Key: KeyIcon,
  PersonAdd: PersonAddIcon,
  Storefront: StorefrontIcon,
} as const;

export function GuideIcon({ name, color = "primary" }: { name: string; color?: "primary" | "action" | "success" }) {
  const Icon = ICONS[name as keyof typeof ICONS] ?? SchoolIcon;
  return <Icon color={color} fontSize="small" />;
}
