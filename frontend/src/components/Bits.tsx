import type { ReactNode } from "react";

export function PageHead({
  kicker,
  title,
  lede,
}: {
  kicker: string;
  title: string;
  lede: string;
}) {
  return (
    <header className="page-head">
      <p className="kicker">{kicker}</p>
      <h1>{title}</h1>
      <p className="lede">{lede}</p>
    </header>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill tone-${tone}`}>{children}</span>;
}

export function Locked({ title, body }: { title: string; body: string }) {
  return (
    <section className="locked">
      <p className="kicker">Capability missing</p>
      <h1>{title}</h1>
      <p>{body}</p>
    </section>
  );
}
