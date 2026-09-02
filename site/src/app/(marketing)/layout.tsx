import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { site } from "@/lib/site";

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "ProfessionalService",
  name: site.legalName,
  alternateName: site.name,
  url: site.url,
  email: site.email,
  image: `${site.url}/opengraph-image`,
  description: site.description,
  areaServed: {
    "@type": "AdministrativeArea",
    name: site.areaServed,
  },
  address: {
    "@type": "PostalAddress",
    addressLocality: "San Francisco Bay Area",
    addressRegion: "CA",
    addressCountry: "US",
  },
  contactPoint: {
    "@type": "ContactPoint",
    email: site.email,
    contactType: "customer service",
    availableLanguage: "English",
  },
  knowsAbout: [
    "Website building for small businesses",
    "Automating business systems",
  ],
  hasOfferCatalog: {
    "@type": "OfferCatalog",
    name: "Güd Vector services",
    itemListElement: site.pillars.map((pillar) => ({
      "@type": "Offer",
      itemOffered: {
        "@type": "Service",
        name: pillar.title,
        description: pillar.body,
        areaServed: site.areaServed,
      },
    })),
  },
};

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:rounded-full focus:bg-brand focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to content
      </a>
      <SiteHeader />
      <main id="main" className="flex-1">
        {children}
      </main>
      <SiteFooter />
    </>
  );
}
