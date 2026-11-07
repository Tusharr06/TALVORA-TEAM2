import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000", follow_redirects=True)
r1 = client.get("/api/v1/auth/login?redirect_target=/desk")
print("1. Login Init:", r1.status_code)
auth_url = r1.json()["authorization_url"]

r2 = client.get(auth_url)
print("2. Dev Authorize & Callback:", r2.status_code, "Final URL:", r2.url)

r3 = client.get("/api/v1/auth/me")
me = r3.json()
print("3. Auth /me:", r3.status_code, "User:", me.get("email"), "Role:", me.get("role"), "Org:", me.get("active_organization_id"))

r4 = client.get("/api/v1/jobs")
jobs = r4.json()["items"]
print(f"4. Jobs ({len(jobs)}):")
for j in jobs:
    r_ver = client.get(f"/api/v1/jobs/{j['id']}/requirement-versions")
    vers = r_ver.json()
    crit_count = len(vers[0]["criteria"]) if vers else 0
    print(f"   - {j['title']} [{j['status']}] -> Versions: {len(vers)}, Criteria: {crit_count}")

r_cand = client.get("/api/v1/candidates")
cands = r_cand.json()["items"]
print(f"5. Candidates ({len(cands)}):")
for c in cands[:3]:
    print(f"   - {c['display_name']} [{c['email']}] (Attested: {c['is_contact_verified']})")

r_apps = client.get("/api/v1/applications")
apps = r_apps.json()["items"]
print(f"6. Applications ({len(apps)}):")
for a in apps[:4]:
    print(f"   - App {a['id'][:8]} -> Stage: {a['stage']}")

r_doc = client.get("/api/v1/documents/a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d/versions/1/source")
print("7. Evidence Source for Elena Rostova Resume:", r_doc.status_code)
if r_doc.status_code == 200:
    source = r_doc.json()
    print(f"   Status: {source['extraction']['status']}, Segments: {len(source['segments'])}")
    for s in source["segments"]:
        print(f"   [Page {s['page_number']}] {s['text'][:80]}...")

