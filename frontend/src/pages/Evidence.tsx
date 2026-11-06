import { useEffect, useState } from "react";
import { Locked, PageHead, Pill } from "../components/Bits";
import { pretty } from "../format";
import { useWorkspace, type SourceView } from "../state";

export function Evidence() {
  const { documents, can, loadSource } = useWorkspace();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = documents.find((doc) => doc.id === selectedId) ?? documents[0] ?? null;
  const [source, setSource] = useState<SourceView | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!selected || !can("READ_DOCUMENTS")) return;
    let cancelled = false;
    setSource(null);
    setProblem(null);
    void loadSource(selected.id, selected.version)
      .then((view) => {
        if (!cancelled) setSource(view);
      })
      .catch((error: unknown) => {
        if (!cancelled) setProblem(error instanceof Error ? error.message : "Source unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [can, loadSource, selected]);

  if (!can("READ_DOCUMENTS")) {
    return (
      <Locked
        title="The bytes stay sealed."
        body="Reading document text requires the document capability. This session does not have it."
      />
    );
  }

  return (
    <>
      <PageHead
        kicker="Evidence"
        title="Quote the page. Do not invent it."
        lede="Source text appears only after extraction finishes on a clean document. A pending or failed run is shown as status, with an empty page."
      />
      {documents.length === 0 ? (
        <p className="banner">
          No document from this session yet. Admit a file on Intake. The identifier stays in this
          browser so you can reopen the source after extraction.
        </p>
      ) : (
        <div className="split">
          <div className="menu">
            {documents.map((doc) => (
              <button
                key={doc.id}
                aria-pressed={selected?.id === doc.id}
                type="button"
                onClick={() => setSelectedId(doc.id)}
              >
                <strong>{doc.filename}</strong>
                <span>
                  {pretty(doc.status)}
                  {doc.problem_code ? ` · ${pretty(doc.problem_code)}` : ""}
                </span>
              </button>
            ))}
          </div>
          <article className="inspector">
            {problem ? <p className="banner">{problem}</p> : null}
            {source ? (
              <>
                <p className="kicker">Version {source.version_number}</p>
                <h2>{selected?.filename}</h2>
                <p>
                  Extraction{" "}
                  <Pill>{source.extraction ? pretty(source.extraction.status) : "Not started"}</Pill>
                </p>
                {source.extraction?.error_detail ? <p>{source.extraction.error_detail}</p> : null}
                {source.segments.length === 0 ? (
                  <p>No segments. Incomplete text is not presented as a history.</p>
                ) : (
                  source.segments.map((segment) => (
                    <blockquote key={segment.id} className="pull">
                      <p>{segment.text}</p>
                      <footer>
                        <span>
                          {segment.page_number ? `Page ${segment.page_number}` : "Locator"} ·{" "}
                          {pretty(segment.extraction_method)}
                        </span>
                      </footer>
                    </blockquote>
                  ))
                )}
              </>
            ) : selected && !problem ? (
              <p>Reading the source…</p>
            ) : null}
          </article>
        </div>
      )}
    </>
  );
}
