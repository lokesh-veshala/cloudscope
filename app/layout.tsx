import type { Metadata } from "next";
import "./globals.css";
import "./live.css";

export const metadata: Metadata = {
  title: "CloudScope — Cloud Cost Governance",
  description: "Near-real-time AWS resource consumption, cost confidence, and team limit monitoring.",
  other: { "codex-preview": "development" },
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({children}:{children:React.ReactNode}) {
  return <html lang="en"><body>{children}</body></html>;
}
