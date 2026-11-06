import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { duplicatePairs, jobById } from "../data";
import { pretty, when } from "../format";
import { useWorkspace } from "../state";

export function People() {
  const { candidates, applications, can, attest, dismissedDupes, dismissDupe } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return candidates.filter((person) => {
      if (!needle) return true;
      const emails = person.contacts.map((item) => item.email).join(" ");
      return `${person.name} ${emails}`.toLowerCase().includes(needle);
    });
  }, [candidates, query]);

  const requested = params.get("id");
  const person = filtered.find((item) => item.id === requested) ?? filtered[0];
  const latest = person?.contacts[person.contacts.length - 1];
  const verified = Boolean(person && latest && person.verification?.revision === latest.version);
  const pair = person
    ? duplicatePairs.find(
        (item) =>
          !dismissedDupes.includes(item.id) && (item.a === person.id || item.b === person.id),
      )
    : undefined;
  const otherId = pair && person ? (pair.a === person.id ? pair.b : pair.a) : undefined;
  const other = candidates.find((item) => item.id === otherId);

  function attestContact() {
    if (!person) return;
    const message = attest(person.id, note);
    setError(message);
    if (!message) setNote("");
  }

  return (
    <div>
      <PageHead
        kicker="People · tenant-local identity"
        title="A person is not an application."
        lede="The same candidate can hold a separate stage on every vacancy. Email is searchable inside this tenant. It is not a global identity, and lookalikes are never merged for you."
      />
      <div className="split">
        <div>
          <label className="field">
            Search name or email
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Try Park, or mailbox.test"
            />
          </label>
          <div className="menu" role="list">
            {filtered.length === 0 ? <p className="empty">No one matches that search.</p> : null}
            {filtered.map((item) => {
              const mail = item.contacts[item.contacts.length - 1]?.email;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={item.id === person?.id}
                  onClick={() => {
                    setParams({ id: item.id });
                    setError(null);
                    setNote("");
                  }}
                >
                  <strong>{item.name}</strong>
                  <em>{mail}</em>
                </button>
              );
            })}
          </div>
        </div>
        {person && latest ? (
          <article className="dossier">
            <header className="statute-head">
              <div>
                <Pill tone="neutral">{pretty(person.source)}</Pill>
                <h2>{person.name}</h2>
                <p className="lede">{person.headline}</p>
              </div>
              <p className="where">
                {person.location}
                <span>{verified ? "Contact attested" : "Contact not attested"}</span>
              </p>
            </header>
            {pair && other ? (
              <aside className="dupe">
                <p className="kicker">Possible duplicate</p>
                <p>{pair.reason}</p>
                <div className="row">
                  <button type="button" className="btn btn-ghost" onClick={() => setParams({ id: other.id })}>
                    Open {other.name}
                  </button>
                  <button type="button" className="btn btn-primary" onClick={() => dismissDupe(pair.id)}>
                    Keep separate
                  </button>
                </div>
              </aside>
            ) : null}
            <h3>Latest contact · revision {latest.version}</h3>
            <p className="email">{latest.email}</p>
            <p className="muted">{latest.phone ?? "No phone on this revision"}</p>
            <p className="hash">{pretty(latest.source)}</p>
            {verified && person.verification ? (
              <p className="banner law-banner">
                Attested by {person.verification.actor} on revision {person.verification.revision} ·{" "}
                {pretty(person.verification.source)}. {person.verification.note}
              </p>
            ) : can("VERIFY_CANDIDATE_CONTACT") ? (
              <label className="field">
                Attest this exact revision
                <textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="How you checked this address. This is a human record, not proof of mailbox ownership."
                />
                <button className="btn btn-primary" type="button" onClick={attestContact}>
                  Attest contact
                </button>
              </label>
            ) : (
              <p className="banner">
                Hiring managers and interviewers can read this log. They do not attest contact.
              </p>
            )}
            {error ? <p className="form-error">{error}</p> : null}
            <h3>Revision log</h3>
            <ol className="timeline">
              {[...person.contacts].reverse().map((item) => (
                <li key={item.version}>
                  <span className="dot" />
                  <div>
                    <strong>
                      v{item.version} · {item.email}
                    </strong>
                    <p className="muted">
                      {pretty(item.source)} · {when(item.at)}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <h3>Applications</h3>
            <ul className="notes">
              {applications.filter((item) => item.candidateId === person.id).length === 0 ? (
                <li className="muted">No application yet. A draft identity can exist before a vacancy claim.</li>
              ) : null}
              {applications
                .filter((item) => item.candidateId === person.id)
                .map((item) => {
                  const job = jobById(item.jobId);
                  return (
                    <li key={item.id}>
                      <Link to={`/pipeline?app=${item.id}`}>
                        {item.serial} · {job?.title}
                      </Link>
                      <p className="muted">{pretty(item.stage)}</p>
                    </li>
                  );
                })}
            </ul>
          </article>
        ) : (
          <p className="empty">Nobody to open.</p>
        )}
      </div>
    </div>
  );
}
