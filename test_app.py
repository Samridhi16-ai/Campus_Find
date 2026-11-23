"""Quick end-to-end test:  python test_app.py"""
import os
import tempfile

import db

tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
db.DB_PATH = tmp.name
db.init_db()

import app as appmod  # noqa: E402

client = appmod.app.test_client()
ADMIN = {"X-Admin-Token": appmod.ADMIN_TOKEN}


def post(url, body, headers=None):
    return client.post(url, json=body, headers=headers or {})


def test_flow():
    lost = post("/api/listings", {
        "type": "lost", "category": "Electronics", "name": "Black wireless earbuds",
        "description": "Black earbuds in a small white case, scratch on the lid",
        "location": "Library 2nd floor", "date": "2026-11-20",
        "reporter": "Asha", "email": "asha@college.edu"})
    assert lost.status_code == 201

    found = post("/api/listings", {
        "type": "found", "category": "Electronics", "name": "Earbuds with white case",
        "description": "Black wireless earbuds, white charging case, scratched lid",
        "location": "Library second floor", "date": "2026-11-21",
        "reporter": "Ravi", "email": "ravi@college.edu"})
    body = found.get_json()
    assert body["suggested_matches"], "matching engine should link the two listings"
    assert body["suggested_matches"][0]["listing"]["type"] == "lost"
    assert "email" not in body["listing"]  # privacy

    lid = lost.get_json()["listing"]["id"]
    claim = post(f"/api/listings/{body['listing']['id']}/claims", {
        "claimant": "Asha", "email": "asha@college.edu", "proof": "Scratch on lid, red sticker inside"})
    code = claim.get_json()["claim_code"]
    assert code.startswith("CF-")

    # code must NOT work before approval
    assert post("/api/admin/verify-code", {"claim_code": code}, ADMIN).status_code == 409
    # admin routes locked without token
    assert post("/api/admin/verify-code", {"claim_code": code}).status_code == 403

    claim_id = client.get("/api/admin/claims", headers=ADMIN).get_json()[0]["id"]
    assert post(f"/api/admin/claims/{claim_id}/approve", {}, ADMIN).status_code == 200
    assert post("/api/admin/verify-code", {"claim_code": code}, ADMIN).status_code == 200

    stats = client.get("/api/stats").get_json()
    assert stats["returned"] == 1 and stats["total"] == 2
    assert lid  # silence linter


def test_validation():
    bad = post("/api/listings", {"type": "lost", "name": "x"})
    assert bad.status_code == 400
    future = post("/api/listings", {
        "type": "lost", "category": "Keys", "name": "Keys", "description": "d",
        "location": "l", "date": "2999-01-01", "reporter": "a", "email": "a@b.co"})
    assert future.status_code == 400


if __name__ == "__main__":
    test_flow()
    test_validation()
    print("All tests passed")
    os.unlink(tmp.name)
