import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Botcrypto4 - Order Flow + Liquidation Sweeps",
  description: "Crypto trading bot focused on detecting liquidation sweeps and confirming with CVD divergence",
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