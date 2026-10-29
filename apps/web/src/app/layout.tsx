import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { AiDisclosure } from "@/components/AiDisclosure";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lanternkeeper",
  description: "Self-hosted solo AI Dungeon Master",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">Skip to content</a>
        <header className="top">
          <Link href="/" className="brand">LANTERNKEEPER</Link>
          <nav aria-label="Primary">
            <Link href="/">Campaigns</Link>
            <Link href="/library">Adventure library</Link>
            <Link href="/rules">Rules coverage</Link>
          </nav>
          <AiDisclosure />
        </header>
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
