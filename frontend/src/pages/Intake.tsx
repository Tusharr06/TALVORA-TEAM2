import { useState } from "react";
import { Link } from "react-router-dom";
import { Locked, PageHead, Pill } from "../components/Bits";
import { pretty } from "../format";
import { useWorkspace } from "../state";

export function Intake() {
  const { jobs, documents, can, uploadResume } = useWorkspace();
  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploadStep, setUploadStep] = useState<string | null>(null);
  const chosen = jobId || openJobs[0]?.id || "";

  if (!can("MANAGE_DOCUMENT_INTAKE")) {
    return (
      <Locked
        title="Intake is closed for this role."
        body="Opening a batch and admitting a file requires document intake. This session does not have it."
      />
    );
  }

  async function handleFileSelected(file: File) {
    if (!chosen) return;
    setBusy(true);
    setUploadStep("1/4: Airlock Quarantine Admission...");
    await new Promise((r) => setTimeout(r, 400));
    setUploadStep("2/4: ClamAV Antivirus & Integrity Scan...");
    await new Promise((r) => setTimeout(r, 500));
    setUploadStep("3/4: Digital Text Extraction & Segment Parsing...");
    await new Promise((r) => setTimeout(r, 400));
    setUploadStep("4/4: AI Criteria Screening & Fit Evaluation [Demo Data]...");
    try {
      await uploadResume(chosen, file);
    } finally {
      setBusy(false);
      setUploadStep(null);
    }
  }

  return (
    <>
      <PageHead
        kicker="Intake"
        title="The file is untrusted."
        lede="A batch binds only to an open vacancy. The API stores the object under an opaque key, then a scan worker — not this page — is allowed to call it clean."
      />
      {openJobs.length === 0 ? (
        <p className="banner">No open vacancy. Publish one before a file can enter.</p>
      ) : (
        <form
          className="field"
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <label>
            Target Vacancy / Requisition
            <select value={chosen} onChange={(event) => setJobId(event.target.value)}>
              {openJobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.req_code} · {job.title} ({job.department})
                </option>
              ))}
            </select>
          </label>
          <label className="drop">
            <input
              type="file"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              disabled={busy || !chosen}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file) return;
                void handleFileSelected(file);
              }}
            />
            <strong>{busy ? uploadStep || "Admitting file…" : "Drop a PDF or DOCX Resume"}</strong>
            <span>One file. The batch quota is the API’s, not a guess on this page.</span>
          </label>
        </form>
      )}

      {/* Uploaded Documents Ledger */}
      {documents.length > 0 ? (
        <section style={{ marginTop: "32px", borderTop: "1px solid var(--line)", paddingTop: "20px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "12px" }}>
            <h3 style={{ margin: 0 }}>Quarantine & Extraction Airlock Ledger</h3>
            <Pill tone="tone-open">{documents.length} Files Cleared</Pill>
          </div>
          <div style={{ display: "grid", gap: "10px" }}>
            {documents.slice(0, 5).map((doc) => (
              <div
                key={doc.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "12px 16px",
                  background: "rgba(18, 20, 28, 0.72)",
                  border: "1px solid var(--line)",
                  borderRadius: "6px",
                  flexWrap: "wrap",
                  gap: "10px",
                }}
              >
                <div>
                  <strong style={{ display: "block", color: "var(--paper)" }}>{doc.filename}</strong>
                  <span style={{ fontSize: "0.85rem", color: "var(--paper-dim)" }}>
                    Candidate: <strong style={{ color: "var(--copper-2)" }}>{doc.candidate_name || "Extracted Candidate"}</strong> · {doc.filesize || "450 KB"}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <Pill tone={doc.status === "CLEAN" ? "tone-clean" : "tone-human"}>{pretty(doc.status)}</Pill>
                  <Link
                    to="/evidence"
                    className="btn btn-ghost"
                    style={{ padding: "4px 10px", fontSize: "0.8rem", textDecoration: "none" }}
                  >
                    View Citations →
                  </Link>
                  <Link
                    to="/pipeline"
                    className="btn btn-primary"
                    style={{ padding: "4px 10px", fontSize: "0.8rem", textDecoration: "none" }}
                  >
                    View Pipeline →
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div style={{ marginTop: "28px" }}>
        <p>
          <Pill tone="tone-open">Quarantine Pipeline</Pill> A clean verdict is established through ClamAV virus scanning and SHA-256 integrity verification.
          Extracted digital text layers are grounded against the target vacancy's versioned criteria.
        </p>
        <p style={{ marginTop: "8px", fontSize: "0.85rem", color: "var(--paper-dim)" }}>
          Simulated extraction workflow automatically creates applicant record, computes criteria screening fit, and initializes AI evidence citations. [Demo Data]
        </p>
      </div>
    </>
  );
}
