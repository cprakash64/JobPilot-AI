import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "JobPilot AI",
  description: "Open-source, compliant AI job-search and application copilot."
};

/**
 * Declares support for BOTH schemes so the browser paints its own chrome
 * (canvas, form controls, scrollbars) correctly from the first frame. Together
 * with `color-scheme: light dark` in globals.css this is what prevents a white
 * flash before the stylesheet applies for a user in dark mode — and it needs no
 * JavaScript, so it cannot cause a hydration mismatch.
 */
export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbf8" },
    { media: "(prefers-color-scheme: dark)", color: "#14171a" }
  ]
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
