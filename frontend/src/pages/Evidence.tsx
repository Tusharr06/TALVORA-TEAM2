import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Locked, PageHead, Pill } from "../components/Bits";
import { runs } from "../data";
import { pretty } from "../format";
import { useWorkspace } from "../state";

export function Evidence() {
  const { candidates, reviewedRuns, ackRun, can } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const requested = params.get("run");
  const run = runs.find((item) => item.id === requested) ?? runs[0];
  const person = candidates.find((item) => item.id === run.candidateId);
  const [fragmentId, setFragmentId] = useState(run.fragments[0]?.id ?? "");
  const fragment = run.fragments.find((item) => item.id === fragmentId) ?? run.fragments[0];
  const reviewed = reviewedRuns.includes(run.id);

  if (!can("READ_DOCUMENTS")) {
    return (
      <Locked
        title="This role does not read source text."
        body="Interviewers work from the application and their own note. Extracted pages, OCR, and file bytes stay with people who hold read-documents."
      />
    );
  }

  return (
    <div>
      <PageHead
        kicker="Evidence · immutable fragments"
        title="Cite the sentence. Do not rewrite it."
        lede="A fragment is a hash-checked excerpt of one segment. A later citation keeps pointing at this revision. Re-extracting creates a new revision. It does not mutate the quote underneath."
      />
      <div className="split lightbox">
        <div className="menu" role="list">
          {runs.map((item) => {
            const name = candidates.find((personItem) => personItem.id === item.candidateId)?.name;
            return (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.id === run.id}
                onClick={() => {
                  setParams({ run: item.id });
                  setFragmentId(item.fragments[0]?.id ?? "");
                }}
              >
                <Pill tone={item.status === "SUCCEEDED" ? "clean" : "human"}>{pretty(item.status)}</Pill>
                <strong>{name}</strong>
                <em>
                  rev {item.revision} · {item.filename}
                </em>
              </button>
            );
          })}
        </div>
        <div>
          <header className="statute-head">
            <div>
              <p className="kicker">{run.extractor}</p>
              <h2>{run.documentTitle}</h2>
              <p className="lede">
                {person ? <Link to={`/people?id=${person.id}`}>{person.name}</Link> : null}
                {" · "}
                {pretty(run.trigger)} · normalization {run.normalization} · pages {run.pages}
                {run.ocrPages ? ` · OCR on ${run.ocrPages}` : ""}
              </p>
            </div>
          </header>
          {run.warning ? <p className="banner">{run.warning}</p> : null}
          <div className="evidence-grid">
            <article className="facsimile" aria-label="Source facsimile">
              <p className="facsimile-file">{run.filename}</p>
              {run.segments.map((segment) => {
                const active = fragment?.segmentId === segment.id;
                return (
                  <section key={segment.id} className={active ? "segment is-hot" : "segment"}>
                    <p className="hash">
                      {segment.locator}
                      {segment.confidence != null ? ` · confidence ${segment.confidence.toFixed(2)}` : ""}
                      {" · "}
                      {pretty(segment.method)}
                    </p>
                    <p>
                      <Quoted text={segment.text} quote={active ? fragment?.quote ?? "" : ""} />
                    </p>
                  </section>
                );
              })}
            </article>
            <div className="fragments">
              {run.fragments.map((item) => {
                const segment = run.segments.find((seg) => seg.id === item.segmentId);
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={item.id === fragment?.id ? "fragment is-on" : "fragment"}
                    onClick={() => setFragmentId(item.id)}
                  >
                    <span className="kicker">Fragment</span>
                    <p>“{item.quote}”</p>
                    <span className="hash">
                      {segment?.locator} · {item.hash}
                    </span>
                  </button>
                );
              })}
              {run.status === "NEEDS_REVIEW" ? (
                reviewed ? (
                  <p className="banner law-banner">Reviewed in this preview. The quote was not edited.</p>
                ) : (
                  <button className="btn btn-primary" type="button" onClick={() => ackRun(run.id)}>
                    I have read this partial extraction
                  </button>
                )
              ) : (
                <p className="muted">This revision succeeded. The highlight is the citable span.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Quoted({ text, quote }: { text: string; quote: string }) {
  if (!quote) return <>{text}</>;
  const index = text.indexOf(quote);
  if (index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark>{quote}</mark>
      {text.slice(index + quote.length)}
    </>
  );
}
