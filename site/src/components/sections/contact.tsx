import { ContactForm } from "@/components/contact-form";
import { site } from "@/lib/site";

export function Contact() {
  return (
    <section id="contact" className="section-orange scroll-mt-24">
      <div className="mx-auto grid w-full max-w-[1100px] items-start gap-10 px-5 py-14 desktop:grid-cols-2 desktop:px-8 desktop:py-20">
        <div>
          <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
            Contact
          </p>
          <h2 className="mt-3 max-w-[16ch] text-3xl font-semibold tracking-[-0.03em] text-charcoal desktop:text-4xl">
            Get in touch.
          </h2>
          <p className="mt-4 max-w-[34rem] text-base leading-relaxed text-neutral-600">
            {site.contactIntro}
          </p>
          <p className="mt-6 text-sm font-semibold tracking-[0.14em] text-neutral-500 uppercase">
            Email
          </p>
          <p className="mt-1 select-all text-2xl font-semibold tracking-tight break-all text-charcoal">
            {site.email}
          </p>
        </div>
        <ContactForm />
      </div>
    </section>
  );
}
