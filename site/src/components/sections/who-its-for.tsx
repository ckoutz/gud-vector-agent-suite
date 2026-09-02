import { site } from "@/lib/site";

export function WhoItsFor() {
  return (
    <section className="section-orange">
      <div className="mx-auto grid w-full max-w-[1100px] gap-8 px-5 py-14 desktop:grid-cols-2 desktop:px-8 desktop:py-20">
        <div>
          <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
            Who it’s for
          </p>
          <h2 className="mt-3 max-w-[16ch] text-3xl font-semibold tracking-[-0.03em] text-charcoal desktop:text-4xl">
            One site, one system, less to manage.
          </h2>
        </div>
        <p
          className="card-peach max-w-[36rem] rounded-[18px] bg-white p-6 text-base leading-relaxed text-neutral-600"
          style={{ backgroundColor: "#ffffff" }}
        >
          {site.whoItsFor}
        </p>
      </div>
    </section>
  );
}
