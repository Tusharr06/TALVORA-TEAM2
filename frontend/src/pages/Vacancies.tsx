import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { jobs, protectedTraits } from "../data";
import { pretty, when } from "../format";

export function Vacancies() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("id");
  const job = jobs.find((item) => item.id === requested) ?? jobs[0];
  const [version, setVersion] = useState<number | "draft" | "latest">("latest");

  useEffect(() => {
    setVersion("latest");
  }, [job.id]);

  const published = job.versions.at(-1);
  const selected =
    version === "draft"
      ? undefined
      : version === "latest"
        ? published
        : job.versions.find((item) => item.number === version);
  const showingDraft = version === "draft" || !selected;
  const criteria = showingDraft ? job.draft : selected.criteria;

  return (
    <div>
      <PageHead
        kicker="Vacancies · requirement versions"
        title="The rule is a version, not a vibe."
        lede="A published requirement version is the law for that vacancy. Draft text screens nobody. Manual rules stay with a person, and protected traits are refused before they can become criteria."
      />
      <div className="split">
        <div className="menu" role="list">
          {jobs.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={item.id === job.id}
              onClick={() => setParams({ id: item.id })}
            >
              <Pill tone={item.status.toLowerCase()}>{pretty(item.status)}</Pill>
              <strong>{item.title}</strong>
              <em>
                {item.department}
                {item.versions.length ? ` · v${item.versions.at(-1)?.number} published` : " · unpublished"}
              </em>
            </button>
          ))}
        </div>
        <article className="statute">
          <header className="statute-head">
            <div>
              <Pill tone={job.status.toLowerCase()}>{pretty(job.status)}</Pill>
              <h2>{job.title}</h2>
              <p className="lede">{job.summary}</p>
            </div>
            <p className="where">
              {job.location}
              <span>{pretty(job.employment)}</span>
              <span>{job.department}</span>
            </p>
          </header>
          <ul className="assignments">
            {job.assignments.map((item) => (
              <li key={item.role}>
                <span>{item.role}</span>
                {item.name}
              </li>
            ))}
          </ul>
          <div className="version-row" role="tablist" aria-label="Requirement versions">
            {job.versions.map((item) => (
              <button
                key={item.number}
                type="button"
                role="tab"
                aria-selected={selected?.number === item.number && version !== "draft"}
                onClick={() => setVersion(item.number)}
              >
                Version {item.number}
                {item.number === published?.number ? " · law" : ""}
              </button>
            ))}
            {job.draft.length ? (
              <button
                type="button"
                role="tab"
                aria-selected={showingDraft}
                onClick={() => setVersion("draft")}
              >
                Draft · not law
              </button>
            ) : null}
          </div>
          {showingDraft ? (
            <p className="banner">
              Unpublished. These lines are notes toward a future version. They cannot accept a batch
              and they cannot screen a person.
            </p>
          ) : (
            <p className="banner law-banner">
              Published {when(selected.publishedAt)}. {selected.note}
            </p>
          )}
          <ol className={showingDraft ? "articles not-law" : "articles"}>
            {criteria.map((item, index) => (
              <li key={item.id} className="article">
                <div className="article-index">
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  {item.human ? <span className="seal">Human check</span> : null}
                </div>
                <div>
                  <div className="article-meta">
                    <Pill tone={item.human ? "human" : "neutral"}>{pretty(item.type)}</Pill>
                    <Pill tone={item.required ? "open" : "draft"}>{item.required ? "Required" : "Preferred"}</Pill>
                  </div>
                  <p className="source">{item.source}</p>
                  <p className="muted">{item.rationale}</p>
                  <p className="hash">{item.detail}</p>
                </div>
              </li>
            ))}
          </ol>
          {job.status !== "OPEN" ? (
            <p className="muted">
              Intake refuses this vacancy while it is {pretty(job.status).toLowerCase()}. Files bind
              only to an open role.
            </p>
          ) : null}
          <footer className="traits">
            <p>Refused as criteria</p>
            <ul>
              {protectedTraits.map((trait) => (
                <li key={trait}>{trait}</li>
              ))}
            </ul>
          </footer>
        </article>
      </div>
    </div>
  );
}
