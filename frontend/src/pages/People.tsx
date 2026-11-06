import { useMemo, useState } from "react";
import { PageHead, Pill } from "../components/Bits";
import { pretty, when } from "../format";
import { useWorkspace } from "../state";

export function People() {
  const { candidates, can, createCandidate, attest } = useWorkspace();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return candidates;
    return candidates.filter((candidate) =>
      `${candidate.display_name ?? ""} ${candidate.email ?? ""}`.toLowerCase().includes(needle),
    );
  }, [candidates, query]);
  const selected = visible.find((candidate) => candidate.id === selectedId) ?? visible[0] ?? null;

  return (
    <>
      <PageHead
        kicker="People"
        title="Email is not an identity."
        lede="Search stays inside this organization. A lookalike is never merged for you. Attestation records what you checked. It does not prove the mailbox."
      />
      <label className="field">
        Search this page
        <input value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <div className="split">
        <div className="menu">
          {visible.map((candidate) => (
            <button
              key={candidate.id}
              aria-pressed={selected?.id === candidate.id}
              type="button"
              onClick={() => setSelectedId(candidate.id)}
            >
              <strong>{candidate.display_name || "Unnamed"}</strong>
              <span>{candidate.email || "No address"}</span>
            </button>
          ))}
          {visible.length === 0 ? <p>No one matches.</p> : null}
        </div>
        <article className="inspector">
          {selected ? (
            <>
              <p className="kicker">{pretty(selected.source)}</p>
              <h2 className="email">{selected.email || "No address on file"}</h2>
              <p>{selected.display_name || "Unnamed draft"}</p>
              <dl className="facts">
                <div>
                  <dt>Contact</dt>
                  <dd>
                    {selected.is_contact_verified ? (
                      <Pill tone="clean">Attested</Pill>
                    ) : (
                      <Pill>Unverified</Pill>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Revision</dt>
                  <dd>{selected.current_contact_revision_number}</dd>
                </div>
                <div>
                  <dt>Added</dt>
                  <dd>{when(selected.created_at)}</dd>
                </div>
                <div>
                  <dt>Record</dt>
                  <dd>v{selected.version}</dd>
                </div>
              </dl>
              {can("VERIFY_CANDIDATE_CONTACT") && selected.email && !selected.is_contact_verified ? (
                <form
                  className="field"
                  onSubmit={(event) => {
                    event.preventDefault();
                    setBusy(true);
                    void attest(selected, notes).finally(() => {
                      setBusy(false);
                      setNotes("");
                    });
                  }}
                >
                  <textarea
                    required
                    minLength={8}
                    placeholder="What you checked before attesting this address"
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                  />
                  <button className="btn btn-primary" type="submit" disabled={busy}>
                    Attest this address
                  </button>
                </form>
              ) : null}
            </>
          ) : (
            <p>Add a person. The address stays local to this organization.</p>
          )}
          {can("CREATE_CANDIDATE") ? (
            <form
              className="field"
              onSubmit={(event) => {
                event.preventDefault();
                setBusy(true);
                void createCandidate({ display_name: name, email }).finally(() => {
                  setBusy(false);
                  setName("");
                  setEmail("");
                });
              }}
            >
              <h3>Add a person</h3>
              <input
                required
                placeholder="Name"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <input
                required
                type="email"
                placeholder="Email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <button className="btn btn-primary" type="submit" disabled={busy}>
                Add
              </button>
            </form>
          ) : null}
        </article>
      </div>
    </>
  );
}
