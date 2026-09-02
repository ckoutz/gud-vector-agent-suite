export const site = {
  name: "Güd Vector",
  legalName: "Güd Vector Consulting Services",
  domain: "gudvector.com",
  url: "https://gudvector.com",
  email: "info@gudvector.com",
  areaServed: "San Francisco Bay Area",
  motto: "Güd Vector — sending your company in the right direction.",
  headline: "If they can't find you, they call someone else.",
  lede: "We build fast, mobile-first websites for local service businesses, plus simple systems to make sure leads, quotes, and follow-ups don't slip through the cracks.",
  description:
    "Fast, mobile-first websites and simple business systems for local service companies in the San Francisco Bay Area. Email info@gudvector.com.",
  metaTitle: "Güd Vector | Websites & Systems for Bay Area Service Businesses",
  pillars: [
    {
      title: "Website building for small business",
      body: "For businesses with no site, or one that isn't pulling its weight. Clean, mobile-first pages with real copy, local SEO, click-to-call, and previews you approve before anything goes live.",
    },
    {
      title: "Automating business systems",
      body: "Practical systems for leads, follow-up, and simple workflows — not enterprise software. Send a quote, and your customer gets a portal to review it, set up service, pay, or pause and cancel — no phone tag, no spreadsheets.",
    },
  ],
  steps: [
    {
      n: "01",
      title: "Tell us what's missing",
      body: "No site, a weak one, or follow-up that keeps falling through.",
    },
    {
      n: "02",
      title: "Get a clear plan",
      body: "See the pages and workflows before we build anything.",
    },
    {
      n: "03",
      title: "Approve on your phone",
      body: "Every preview is phone-ready, because that's how your customers will see it.",
    },
    {
      n: "04",
      title: "Quote to setup, in one place",
      body: "You send the quote. Your customer reviews it, sets up service, and pays — all through a simple portal. They can pause or cancel the same way.",
    },
    {
      n: "05",
      title: "Go live",
      body: "Launch a site and systems your team can actually run day to day.",
    },
  ],
  whoItsFor:
    "Business owners who want running their business to be simple. If you're chasing quotes, hunting for leads, or juggling separate tools for payments and follow-up, this is for you — one site, one system, less to manage.",
  about:
    "Güd Vector Consulting Services is a San Francisco Bay Area shop. Our motto: sending your company in the right direction. We build websites for local service businesses and set up the practical systems that keep leads from going cold — real copy, phone-ready pages, quotes and payments through Stripe, and a customer portal for setup, payment, and cancellation.",
  contactIntro:
    "The only public contact is email. Say whether you need a website, help automating business systems, or both.",
  needs: [
    "Website",
    "Automating business systems",
    "Both",
  ] as const,
} as const;

export const nav = [
  { href: "/#services", label: "Services" },
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#about", label: "About" },
  { href: "/#contact", label: "Contact" },
  { href: "/portal", label: "Customer portal" },
] as const;
