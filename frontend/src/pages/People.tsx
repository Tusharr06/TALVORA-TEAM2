import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { pretty, when } from "../format";
import { useWorkspace } from "../state";

export function People() {
  const { candidates, jobs, applications, can, createCandidate, updateCandidate, createApplication, attest } = useWorkspace();
  const [query, setQuery] = useState("");
  const [filterType, setFilterType] = useState<"all" | "verified" | "unverified" | "pipeline">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [applyJobId, setApplyJobId] = useState("");
  const [busy, setBusy] = useState(false);

  // Edit Candidate State
  const [isEditing, setIsEditing] = useState(false);
  const [editCompany, setEditCompany] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editYears, setEditYears] = useState(5);
  const [editLocation, setEditLocation] = useState("");
  const [editPhone, setEditPhone] = useState("");

  const visible = useMemo(() => {
    return candidates.filter((candidate) => {
      if (filterType === "verified" && !candidate.is_contact_verified) return false;
      if (filterType === "unverified" && candidate.is_contact_verified) return false;
      if (filterType === "pipeline" && !applications.some((a) => a.candidate_id === candidate.id)) return false;

      const needle = query.trim().toLowerCase();
      if (!needle) return true;
      const matchName = (candidate.display_name ?? "").toLowerCase().includes(needle);
      const matchEmail = (candidate.email ?? "").toLowerCase().includes(needle);
      const matchCompany = (candidate.current_company ?? "").toLowerCase().includes(needle);
      const matchTitle = (candidate.current_title ?? "").toLowerCase().includes(needle);
      return matchName || matchEmail || matchCompany || matchTitle;
    });
  }, [candidates, query, filterType, applications]);

  const selected = visible.find((candidate) => candidate.id === selectedId) ?? visible[0] ?? null;
  const candidateApplication = selected ? applications.find((a) => a.candidate_id === selected.id) : null;
  const applicationJob = candidateApplication ? jobs.find((j) => j.id === candidateApplication.job_id) : null;
  const openJobs = jobs.filter((j) => j.status === "OPEN");

  function startEditing() {
    if (!selected) return;
    setEditCompany(selected.current_company || "");
    setEditTitle(selected.current_title || "");
    setEditYears(selected.experience_years || 5);
    setEditLocation(selected.location || "");
    setEditPhone(selected.phone || "");
    setIsEditing(true);
  }

  async function handleSaveCandidate() {
    if (!selected) return;
    setBusy(true);
    try {
      await updateCandidate(selected.id, {
        current_company: editCompany,
        current_title: editTitle,
        experience_years: Number(editYears) || 0,
        location: editLocation,
        phone: editPhone,
      });
      setIsEditing(false);
    } finally {
      setBusy(false);
    }
  }

  async function handleDirectApply() {
    if (!selected || !applyJobId) return;
    setBusy(true);
    try {
      await createApplication(applyJobId, selected.id);
      setApplyJobId("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead
        kicker="People"
        title="Email is not an identity."
        lede="Search stays inside this organization. A lookalike is never merged for you. Attestation records what you checked. It does not prove the mailbox."
      />

      {/* Filter Tabs & Search Bar */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center", marginBottom: "18px" }}>
        <div style={{ display: "flex", gap: "6px" }}>
          <button
            className="btn btn-ghost"
            type="button"
            aria-pressed={filterType === "all"}
            onClick={() => setFilterType("all")}
            style={{ padding: "6px 12px", fontSize: "0.85rem" }}
          >
            All ({candidates.length})
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            aria-pressed={filterType === "verified"}
            onClick={() => setFilterType("verified")}
            style={{ padding: "6px 12px", fontSize: "0.85rem" }}
          >
            Verified ({candidates.filter((c) => c.is_contact_verified).length})
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            aria-pressed={filterType === "unverified"}
            onClick={() => setFilterType("unverified")}
            style={{ padding: "6px 12px", fontSize: "0.85rem" }}
          >
            Unverified ({candidates.filter((c) => !c.is_contact_verified).length})
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            aria-pressed={filterType === "pipeline"}
            onClick={() => setFilterType("pipeline")}
            style={{ padding: "6px 12px", fontSize: "0.85rem" }}
          >
            In Pipeline ({candidates.filter((c) => applications.some((a) => a.candidate_id === c.id)).length})
          </button>
        </div>

        <div style={{ flex: "1 1 240px", maxWidth: "380px", marginLeft: "auto" }}>
          <input
            placeholder="Search candidate name, company, or email..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            style={{
              width: "100%",
              background: "#0c0d12",
              color: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              padding: "7px 12px",
              fontSize: "0.9rem",
            }}
          />
        </div>
      </div>

      <div className="split">
        <div className="menu">
          {visible.map((candidate) => (
            <button
              key={candidate.id}
              aria-pressed={selected?.id === candidate.id}
              type="button"
              onClick={() => {
                setSelectedId(candidate.id);
                setIsEditing(false);
              }}
              style={{ textAlign: "left", padding: "14px 16px" }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>{candidate.display_name || "Unnamed"}</strong>
                {candidate.is_contact_verified ? (
                  <Pill tone="tone-open">Attested</Pill>
                ) : (
                  <Pill tone="tone-human">Unverified</Pill>
                )}
              </div>
              <span style={{ color: "var(--copper-2)", fontSize: "0.82rem", display: "block", marginTop: "2px" }}>
                {candidate.current_title ? `${candidate.current_title} @ ${candidate.current_company}` : candidate.email}
              </span>
            </button>
          ))}
          {visible.length === 0 ? <p className="empty">No candidates match your criteria.</p> : null}
        </div>

        <article className="inspector">
          {selected ? (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "10px" }}>
                <div>
                  <p className="kicker">{pretty(selected.source)}</p>
                  <h2 style={{ fontSize: "1.7rem" }}>{selected.display_name || "Unnamed draft"}</h2>
                  <p style={{ color: "var(--copper-2)", fontSize: "1rem", marginTop: "2px" }}>
                    {selected.current_title} @ {selected.current_company}
                  </p>
                </div>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                    onClick={() => {
                      if (isEditing) {
                        setIsEditing(false);
                      } else {
                        startEditing();
                      }
                    }}
                  >
                    {isEditing ? "Cancel" : "Edit Dossier"}
                  </button>
                  {candidateApplication ? (
                    <Link
                      to={`/pipeline?app=${candidateApplication.id}`}
                      className="btn btn-primary"
                      style={{ padding: "4px 12px", fontSize: "0.8rem", textDecoration: "none" }}
                    >
                      View in Pipeline →
                    </Link>
                  ) : null}
                </div>
              </div>

              {isEditing ? (
                <div style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--line)", padding: "16px", borderRadius: "6px", margin: "14px 0" }}>
                  <h3 style={{ marginTop: 0 }}>Edit Candidate Profile Details</h3>
                  <div style={{ display: "grid", gap: "10px" }}>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                      <label className="field" style={{ margin: 0 }}>
                        Current Company
                        <input value={editCompany} onChange={(e) => setEditCompany(e.target.value)} />
                      </label>
                      <label className="field" style={{ margin: 0 }}>
                        Current Role Title
                        <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
                      </label>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px" }}>
                      <label className="field" style={{ margin: 0 }}>
                        Experience Years
                        <input type="number" value={editYears} onChange={(e) => setEditYears(Number(e.target.value))} />
                      </label>
                      <label className="field" style={{ margin: 0 }}>
                        Location
                        <input value={editLocation} onChange={(e) => setEditLocation(e.target.value)} />
                      </label>
                      <label className="field" style={{ margin: 0 }}>
                        Phone
                        <input value={editPhone} onChange={(e) => setEditPhone(e.target.value)} />
                      </label>
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "8px" }}>
                      <button className="btn btn-ghost" type="button" onClick={() => setIsEditing(false)}>
                        Cancel
                      </button>
                      <button className="btn btn-primary" type="button" disabled={busy} onClick={() => void handleSaveCandidate()}>
                        Save Profile
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

              <dl className="facts" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", marginTop: "16px" }}>
                <div>
                  <dt>Email Address</dt>
                  <dd style={{ fontFamily: "var(--mono)", color: "var(--aurora)" }}>{selected.email || "No address"}</dd>
                </div>
                <div>
                  <dt>Phone Contact</dt>
                  <dd>{selected.phone || "—"}</dd>
                </div>
                <div>
                  <dt>Primary Location</dt>
                  <dd>{selected.location}</dd>
                </div>
                <div>
                  <dt>Professional Track Record</dt>
                  <dd>{selected.experience_years} Years Experience</dd>
                </div>
                <div>
                  <dt>Education & Degrees</dt>
                  <dd>{selected.education}</dd>
                </div>
                <div>
                  <dt>Identity Status</dt>
                  <dd>
                    {selected.is_contact_verified ? (
                      <Pill tone="tone-open">Attested by Recruiter</Pill>
                    ) : (
                      <Pill tone="tone-human">Unverified Mailbox</Pill>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Active Requisition</dt>
                  <dd>
                    {applicationJob ? (
                      <span style={{ color: "var(--copper-2)" }}>{applicationJob.title}</span>
                    ) : (
                      <span style={{ color: "var(--paper-dim)" }}>No active application</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Record Added</dt>
                  <dd>{when(selected.created_at)}</dd>
                </div>
                <div>
                  <dt>Recruiter Rating</dt>
                  <dd style={{ color: "var(--gold)" }}>{selected.rating}</dd>
                </div>
              </dl>

              {/* Submit Candidate to Open Vacancy if not currently in pipeline */}
              {!candidateApplication ? (
                <div style={{ marginTop: "20px", padding: "14px", border: "1px solid var(--line)", background: "rgba(255, 255, 255, 0.02)", borderRadius: "6px" }}>
                  <h3 style={{ margin: "0 0 10px 0" }}>Open Application for Candidate</h3>
                  <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
                    <select
                      value={applyJobId}
                      onChange={(e) => setApplyJobId(e.target.value)}
                      style={{ background: "#0c0d12", color: "var(--paper)", border: "1px solid var(--line)", padding: "7px 12px", borderRadius: "6px", flex: "1 1 220px" }}
                    >
                      <option value="">Select Open Requisition...</option>
                      {openJobs.map((job) => (
                        <option key={job.id} value={job.id}>
                          {job.req_code} · {job.title}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn btn-primary"
                      type="button"
                      disabled={busy || !applyJobId}
                      onClick={() => void handleDirectApply()}
                    >
                      Submit to Pipeline
                    </button>
                  </div>
                </div>
              ) : null}

              {/* Attestation Form */}
              {can("VERIFY_CANDIDATE_CONTACT") && selected.email && !selected.is_contact_verified ? (
                <form
                  className="field"
                  style={{ marginTop: "20px", borderTop: "1px solid var(--line)", paddingTop: "14px" }}
                  onSubmit={(event) => {
                    event.preventDefault();
                    setBusy(true);
                    void attest(selected, notes).finally(() => {
                      setBusy(false);
                      setNotes("");
                    });
                  }}
                >
                  <h3 style={{ marginTop: 0 }}>Human Contact Attestation</h3>
                  <p style={{ fontSize: "0.85rem", color: "var(--paper-dim)", margin: "0 0 8px 0" }}>
                    Record evidence checked to prove identity before initiating interview loops.
                  </p>
                  <textarea
                    required
                    minLength={8}
                    placeholder="e.g. Verified corporate email domain, LinkedIn identity, and phone callback."
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
              style={{ marginTop: "30px", borderTop: "1px solid var(--line)", paddingTop: "18px" }}
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
              <h3 style={{ marginTop: 0 }}>Add a person</h3>
              <input
                required
                placeholder="Full Name (e.g. Dr. Aris Thorne)"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <input
                required
                type="email"
                placeholder="Corporate or Personal Email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <button className="btn btn-primary" type="submit" disabled={busy || !name || !email}>
                Add to Talent Pool
              </button>
            </form>
          ) : null}
        </article>
      </div>
    </>
  );
}
