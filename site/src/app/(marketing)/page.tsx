import { About } from "@/components/sections/about";
import { Contact } from "@/components/sections/contact";
import { Hero } from "@/components/sections/hero";
import { HowItWorks } from "@/components/sections/how-it-works";
import { Services } from "@/components/sections/services";
import { WhoItsFor } from "@/components/sections/who-its-for";

export default function Home() {
  return (
    <>
      <Hero />
      <Services />
      <HowItWorks />
      <WhoItsFor />
      <About />
      <Contact />
    </>
  );
}
