import { site } from "@/lib/site";

export function Services() {
  return (
    <section id="services" className="section-orange scroll-mt-24">
      <div className="mx-auto w-full max-w-[1100px] px-5 py-14 desktop:px-8 desktop:py-20">
        <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
          Services
        </p>
        <h2 className="mt-3 max-w-[16ch] text-3xl font-semibold tracking-[-0.03em] text-charcoal desktop:text-4xl">
          Two pillars.
        </h2>

        <div className="mt-10 grid gap-5 desktop:grid-cols-2">
          {site.pillars.map((pillar) => (
            <article
              key={pillar.title}
              className="card-peach rounded-[18px] bg-white p-6"
              style={{ backgroundColor: "#ffffff" }}
            >
              <h3 className="text-xl font-semibold text-charcoal">
                {pillar.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-neutral-600">
                {pillar.body}
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
