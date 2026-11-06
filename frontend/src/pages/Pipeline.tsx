import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { jobById } from "../data";
import {
  ACTIVE_STAGES,
  ALLOWED,
  TERMINAL_STAGES,
  destinationLabel,
  interviewEligible,
  needsReason,
  pretty,
  when,
  type Stage,
} from "../format";
import { useWorkspace } from "../state";

export function Pipeline() {
  const { applications, candidates, can, transition, feedback } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const jobFilter = params.get("job") ?? "all";
  const selectedId = params.get("app");
  const [target, setTarget] = useState<Stage | null>(null);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const visible = applications.filter((item) => jobFilter === "all" || item.jobId === jobFilter);
  const selected = applications.find((item) => item.id === selectedId) ?? null;
  const person = candidates.find((item) => item.id === selected?.candidateId);
  const job = selected ? jobById(selected.jobId) : undefined;

  function select(id: string) {
    const next = new URLSearchParams(params);
    next.set("app", id);
    setParams(next);
    setTarget(null);
    setReason("");
    setError(null);
  }

  function writeStage() {
    if (!selected || !target) {
      setError("Choose the stage you are actually writing.");
      return;
    }
    const message = transition(selected.id, target, reason);
    setError(message);
    if (!message) {
      setTarget(null);
      setReason("");
    }
  }

  function fileNote() {
    if (!selected) return;
    const message = feedback(selected.id, note);
    setError(message);
    if (!message) setNote("");
  }

  return (
    <div>
      <PageHead
        kicker="Pipeline · human commands"
        title="Stages move when a person writes them."
        lede="Rejection, withdrawal, and reopening require a reason. Hired is final. Interviewers can leave a note, and the note does not change the stage."
      />
      <div className="filters" role="toolbar" aria-label="Filter by vacancy">
        <FilterButton
          pressed={jobFilter === "all"}
          onClick={() => setParams(selectedId ? { app: selectedId } : {})}
          label="All motion"
        />
        {["job-dse", "job-extract", "job-ops", "job-design"].map((id) => {
          const item = jobById(id);
          if (!item) return null;
          return (
            <FilterButton
              key={id}
              pressed={jobFilter === id}
              onClick={() => {
                const next: Record<string, string> = { job: id };
                if (selectedId) next.app = selectedId;
                setParams(next);
              }}
              label={item.title}
            />
          );
        })}
      </div>
      <div className="flow">
        <div>
          <div className="board-scroll">
            <div className="board">
              {ACTIVE_STAGES.map((stage) => {
                const cards = visible.filter((item) => item.stage === stage);
                return (
                  <section key={stage} className="column" aria-label={pretty(stage)}>
                    <header>
                      <span>{pretty(stage)}</span>
                      <b>{cards.length}</b>
                    </header>
                    {cards.length === 0 ? <p className="empty">No one here.</p> : null}
                    {cards.map((item) => {
                      const name = candidates.find((personItem) => personItem.id === item.candidateId)?.name;
                      return (
                        <article key={item.id} className={item.id === selectedId ? "ticket is-on" : "ticket"}>
                          <button type="button" onClick={() => select(item.id)}>
                            <span className="serial">{item.serial}</span>
                            <strong>{name}</strong>
                            <span>{pretty(item.source)}</span>
                            {item.attention ? <Pill tone="human">Human check</Pill> : null}
                          </button>
                        </article>
                      );
                    })}
                  </section>
                );
              })}
            </div>
          </div>
          <section className="closed-book">
            <h2>Closed book</h2>
            <div className="terminal-grid">
              {TERMINAL_STAGES.map((stage) =>
                visible
                  .filter((item) => item.stage === stage)
                  .map((item) => {
                    const name = candidates.find((personItem) => personItem.id === item.candidateId)?.name;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className={item.id === selectedId ? "ticket is-on" : "ticket"}
                        onClick={() => select(item.id)}
                      >
                        <span className="serial">{item.serial}</span>
                        <strong>{name}</strong>
                        <Pill tone={stage.toLowerCase()}>{pretty(stage)}</Pill>
                      </button>
                    );
                  }),
              )}
            </div>
          </section>
        </div>
        <aside className="inspector" aria-live="polite">
          {!selected || !person || !job ? (
            <p className="lede">Select a ticket. The stage history opens here, and so does the next legal move.</p>
          ) : (
            <>
              <p className="kicker">{selected.serial}</p>
              <h2>{person.name}</h2>
              <p>
                <Link to={`/people?id=${person.id}`}>Open the person</Link>
                {" · "}
                <Link to={`/vacancies?id=${job.id}`}>{job.title}</Link>
              </p>
              <dl className="facts">
                <div>
                  <dt>Stage</dt>
                  <dd>
                    <Pill tone={selected.stage.toLowerCase()}>{pretty(selected.stage)}</Pill>
                  </dd>
                </div>
                <div>
                  <dt>Record version</dt>
                  <dd>{selected.version}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>{pretty(selected.source)}</dd>
                </div>
                <div>
                  <dt>Scheduling</dt>
                  <dd>
                    {interviewEligible(selected.stage)
                      ? "Eligible for a later scheduling pass."
                      : "Not eligible for scheduling at this stage."}
                  </dd>
                </div>
              </dl>
              {selected.attention ? <p className="banner">{selected.attention}</p> : null}
              <h3>History</h3>
              <ol className="timeline">
                {selected.history.map((event) => (
                  <li key={event.id}>
                    <span className="dot" />
                    <div>
                      <strong>
                        {event.from ? `${pretty(event.from)} → ${pretty(event.to)}` : `Opened · ${pretty(event.to)}`}
                      </strong>
                      <p className="muted">
                        {event.actor} · {when(event.at)}
                      </p>
                      {event.reason ? <p>{event.reason}</p> : null}
                    </div>
                  </li>
                ))}
              </ol>
              {can("TRANSITION_STAGE") ? (
                <div className="write-stage">
                  <h3>Write the next stage</h3>
                  {selected.stage === "HIRED" ? (
                    <p className="muted">Hired is final in this phase. There is no reopen.</p>
                  ) : (
                    <>
                      <div className="choices">
                        {ALLOWED[selected.stage].map((stage) => (
                            <button
                              key={stage}
                              type="button"
                              className="btn btn-ghost"
                              aria-pressed={target === stage}
                              onClick={() => {
                                setTarget(stage);
                                setError(null);
                              }}
                            >
                              {destinationLabel(selected.stage, stage)}
                            </button>
                          ))}
                      </div>
                      {target && needsReason(selected.stage, target) ? (
                        <label className="field">
                          Reason
                          <textarea
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            placeholder="Why this person, why this stage, in a sentence a later reader can trust."
                          />
                        </label>
                      ) : null}
                      <button className="btn btn-primary" type="button" onClick={writeStage} disabled={!target}>
                        Write this stage
                      </button>
                    </>
                  )}
                </div>
              ) : (
                <p className="banner">Interviewers do not move stages. Leave the note below.</p>
              )}
              <h3>Feedback</h3>
              <ul className="notes">
                {selected.feedback.length === 0 ? <li className="muted">No notes yet.</li> : null}
                {selected.feedback.map((item) => (
                  <li key={item.id}>
                    <p>{item.body}</p>
                    <p className="muted">
                      {item.author} · {when(item.at)}
                    </p>
                  </li>
                ))}
              </ul>
              {can("SUBMIT_FEEDBACK") ? (
                <label className="field">
                  Add a note
                  <textarea
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="What should the next person know? This stays off the stage."
                  />
                  <button className="btn btn-ghost" type="button" onClick={fileNote}>
                    File feedback
                  </button>
                </label>
              ) : null}
              {error ? <p className="form-error">{error}</p> : null}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function FilterButton({
  pressed,
  onClick,
  label,
}: {
  pressed: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button type="button" className="btn btn-ghost" aria-pressed={pressed} onClick={onClick}>
      {label}
    </button>
  );
}
