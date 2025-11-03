import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Botcrypto4",
  description: "Monorepo scaffold with Next.js frontend and FastAPI backend",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
