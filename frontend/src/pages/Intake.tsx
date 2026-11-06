import { useState } from "react";
import { Locked, PageHead, Pill } from "../components/Bits";
import { pretty } from "../format";
import { useWorkspace } from "../state";

export function Intake() {
  const { jobs, can, uploadResume } = useWorkspace();
  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = jobId || openJobs[0]?.id || "";

  if (!can("MANAGE_DOCUMENT_INTAKE")) {
    return (
      <Locked
        title="Intake is closed for this role."
        body="Opening a batch and admitting a file requires document intake. This session does not have it."
      />
    );
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
            Vacancy
            <select value={chosen} onChange={(event) => setJobId(event.target.value)}>
              {openJobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title}
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
                if (!file || !chosen) return;
                setBusy(true);
                void uploadResume(chosen, file).finally(() => setBusy(false));
              }}
            />
            <strong>{busy ? "Admitting…" : "Drop a PDF or DOCX"}</strong>
            <span>One file. The batch quota is the API’s, not a guess on this page.</span>
          </label>
        </form>
      )}
      <p>
        <Pill>Quarantine</Pill> A clean verdict waits on the scan worker and ClamAV. Until then the
        item stays in the airlock, and Evidence has no text to show.
      </p>
      <p>Storage status you will see: {pretty("QUARANTINED")}, {pretty("SCANNING")}, {pretty("CLEAN")}, {pretty("BLOCKED")}.</p>
    </>
  );
}
