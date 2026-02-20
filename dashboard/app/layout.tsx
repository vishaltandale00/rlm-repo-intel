import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RLM Repo Intel — Live PR Analysis",
  description:
    "Recursive Language Model-powered repository intelligence dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
