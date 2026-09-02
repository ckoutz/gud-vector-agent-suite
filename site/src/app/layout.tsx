import type { Metadata, Viewport } from "next";
import { Fredoka, Geist_Mono, Nunito } from "next/font/google";

import { site } from "@/lib/site";

import "./globals.css";

const heading = Fredoka({
  subsets: ["latin"],
  variable: "--font-fredoka",
  display: "swap",
});

const sans = Nunito({
  subsets: ["latin", "latin-ext"],
  variable: "--font-nunito",
  display: "swap",
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(site.url),
  title: site.metaTitle,
  description: site.description,
  applicationName: site.legalName,
  keywords: [
    "Güd Vector",
    "Gud Vector",
    "website building for small businesses",
    "automating business systems",
    "San Francisco Bay Area",
    "customer portal",
    "Stripe quotes",
  ],
  formatDetection: {
    telephone: false,
    email: true,
    address: false,
  },
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    locale: "en_US",
    url: site.url,
    siteName: site.name,
    title: site.metaTitle,
    description: site.description,
  },
  twitter: {
    card: "summary_large_image",
    title: site.metaTitle,
    description: site.description,
  },
  robots: {
    index: true,
    follow: true,
  },
  category: "business",
};

export const viewport: Viewport = {
  themeColor: "#fdfdfd",
  colorScheme: "light",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${heading.variable} ${sans.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-[#FDFDFD] font-sans text-ink">
        {children}
      </body>
    </html>
  );
}
