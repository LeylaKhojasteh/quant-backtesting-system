import type { Metadata } from "next";
import "./globals.css";
import { RunProvider } from "./run-context";
import { BridgeProvider } from "./bridge-status";
import { RunJobProvider } from "./run-job-context";
import { WorkspaceProvider } from "./workspace-context";

export const metadata: Metadata = {
  title: "Quant Backtest Lab",
  description: "Interactive quantitative backtesting and trade-debugging research prototype.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        <RunProvider>
          <BridgeProvider>
            <RunJobProvider>
              <WorkspaceProvider>{children}</WorkspaceProvider>
            </RunJobProvider>
          </BridgeProvider>
        </RunProvider>
      </body>
    </html>
  );
}
