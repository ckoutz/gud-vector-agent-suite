import { site } from "@/lib/site";

export function HowItWorks() {
  return (
    <section id="how-it-works" className="section-white scroll-mt-24">
      <div className="mx-auto w-full max-w-[1100px] px-5 py-14 desktop:px-8 desktop:py-20">
        <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
          How it works
        </p>
        <h2 className="mt-3 max-w-[18ch] text-3xl font-semibold tracking-[-0.03em] text-charcoal desktop:text-4xl">
          Five short steps.
        </h2>
        <ol className="mt-10 grid gap-4 sm:grid-cols-2 desktop:grid-cols-5">
          {site.steps.map((step) => (
            <li
              key={step.n}
              className="card-peach rounded-[18px] bg-white p-5"
              style={{ backgroundColor: "#ffffff" }}
            >
              <span className="text-[0.7rem] font-semibold tracking-[0.14em] text-brand">
                {step.n}
              </span>
              <h3 className="mt-3 text-base font-semibold text-charcoal">
                {step.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-neutral-600">
                {step.body}
              </p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
