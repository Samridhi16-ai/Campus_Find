"""CampusFind backend (Flask + SQLite).

Run:  python app.py
Admin endpoints need the header  X-Admin-Token: <ADMIN_TOKEN>
(set ADMIN_TOKEN env var; default is "admin123" for demo only).
"""
import os
import re
import secrets
import string
from datetime import date

from flask import Flask, jsonify, request

from db import get_conn, init_db
from matching import find_matches

app = Flask(__name__)

# Initialize SQLite when the module is imported (also happens under Gunicorn).
# This is important because Render commonly starts Flask with: gunicorn app:app
init_db()


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.after_request
def add_cors(resp):
    """Let the GitHub Pages frontend call this API from the browser."""
    resp.headers["Access-Control-Allow-Origin"] = os.environ.get("ALLOWED_ORIGIN", "*")
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Token"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin123")
CATEGORIES = {"Electronics", "Keys", "Cards & ID", "Bags", "Books & Notes",
              "Clothing", "Accessories", "Other"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_PHOTO_CHARS = 1_500_000  # ~1MB base64 data URL


# ---------- helpers ----------
def err(message, code=400):
    return jsonify({"error": message}), code


def require_admin():
    token = request.headers.get("X-Admin-Token", "")
    return secrets.compare_digest(token, ADMIN_TOKEN)


def new_claim_code(conn):
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    while True:
        code = "CF-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not conn.execute("SELECT 1 FROM claims WHERE claim_code=?", (code,)).fetchone():
            return code


def public_listing(row):
    """Listing as shown to everyone: never expose the reporter's email."""
    d = dict(row)
    d.pop("email", None)
    return d


# ---------- listings ----------
@app.get("/api/listings")
def list_listings():
    q = (request.args.get("q") or "").strip().lower()
    ltype = request.args.get("type")          # lost | found
    status = request.args.get("status")       # active | returned
    category = request.args.get("category")
    order = "ASC" if request.args.get("sort") == "oldest" else "DESC"

    sql, params = "SELECT * FROM listings WHERE 1=1", []
    if ltype in ("lost", "found"):
        sql += " AND type=?"; params.append(ltype)
    if status in ("active", "returned"):
        sql += " AND status=?"; params.append(status)
    if category and category != "all":
        sql += " AND category=?"; params.append(category)
    if q:
        sql += " AND (lower(name) LIKE ? OR lower(description) LIKE ? OR lower(location) LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += f" ORDER BY created_at {order}, id {order}"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify([public_listing(r) for r in rows])


@app.post("/api/listings")
def create_listing():
    data = request.get_json(silent=True) or {}
    required = ["type", "category", "name", "description", "location", "date", "reporter", "email"]
    missing = [f for f in required if not str(data.get(f, "")).strip()]
    if missing:
        return err(f"Missing fields: {', '.join(missing)}")
    if data["type"] not in ("lost", "found"):
        return err("type must be 'lost' or 'found'")
    if data["category"] not in CATEGORIES:
        return err("Unknown category")
    if not EMAIL_RE.match(data["email"].strip()):
        return err("Enter a valid college email")
    try:
        if date.fromisoformat(data["date"]) > date.today():
            return err("Date cannot be in the future")
    except ValueError:
        return err("Date must be YYYY-MM-DD")
    photo = data.get("photo")
    if photo and len(photo) > MAX_PHOTO_CHARS:
        return err("Photo too large (max ~1MB)")

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO listings (type, category, name, description, location, date,
                                     reporter, email, photo)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (data["type"], data["category"], data["name"].strip(), data["description"].strip(),
             data["location"].strip(), data["date"], data["reporter"].strip(),
             data["email"].strip().lower(), photo),
        )
        row = conn.execute("SELECT * FROM listings WHERE id=?", (cur.lastrowid,)).fetchone()
        everything = [dict(r) for r in conn.execute("SELECT * FROM listings").fetchall()]

    matches = find_matches(dict(row), everything)
    return jsonify({
        "listing": public_listing(row),
        "suggested_matches": [{"score": m["score"], "listing": public_listing(m["listing"])}
                              for m in matches],
    }), 201


@app.get("/api/listings/<int:listing_id>")
def get_listing(listing_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    if not row:
        return err("Listing not found", 404)
    return jsonify(public_listing(row))


@app.get("/api/listings/<int:listing_id>/matches")
def listing_matches(listing_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not row:
            return err("Listing not found", 404)
        everything = [dict(r) for r in conn.execute("SELECT * FROM listings").fetchall()]
    matches = find_matches(dict(row), everything)
    return jsonify([{"score": m["score"], "listing": public_listing(m["listing"])}
                    for m in matches])


@app.delete("/api/listings/<int:listing_id>")
def delete_listing(listing_id):
    if not require_admin():
        return err("Admin only", 403)
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM listings WHERE id=?", (listing_id,))
    return ("", 204) if cur.rowcount else err("Listing not found", 404)


# ---------- claims ----------
@app.post("/api/listings/<int:listing_id>/claims")
def submit_claim(listing_id):
    data = request.get_json(silent=True) or {}
    for f in ("claimant", "email", "proof"):
        if not str(data.get(f, "")).strip():
            return err(f"Missing field: {f}")
    if not EMAIL_RE.match(data["email"].strip()):
        return err("Enter a valid college email")

    with get_conn() as conn:
        listing = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not listing:
            return err("Listing not found", 404)
        if listing["status"] != "active":
            return err("This item has already been returned", 409)
        code = new_claim_code(conn)
        conn.execute(
            "INSERT INTO claims (listing_id, claimant, email, proof, claim_code) VALUES (?,?,?,?,?)",
            (listing_id, data["claimant"].strip(), data["email"].strip().lower(),
             data["proof"].strip(), code),
        )
    # The code is given to the claimant now, but is only valid for handover
    # after an admin approves the claim.
    return jsonify({"claim_code": code, "status": "pending",
                    "message": "Claim sent. Show this code at the campus office once approved."}), 201


@app.get("/api/admin/claims")
def admin_claims():
    if not require_admin():
        return err("Admin only", 403)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*, l.name AS item_name, l.type AS item_type
               FROM claims c JOIN listings l ON l.id = c.listing_id
               ORDER BY c.created_at DESC"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/admin/claims/<int:claim_id>/<action>")
def admin_review_claim(claim_id, action):
    """action = approve | reject"""
    if not require_admin():
        return err("Admin only", 403)
    if action not in ("approve", "reject"):
        return err("Action must be approve or reject")
    new_status = "approved" if action == "approve" else "rejected"
    with get_conn() as conn:
        cur = conn.execute("UPDATE claims SET status=? WHERE id=? AND status='pending'",
                           (new_status, claim_id))
    return jsonify({"status": new_status}) if cur.rowcount else err("Claim not found or already reviewed", 404)


@app.post("/api/admin/verify-code")
def admin_verify_code():
    """Admin types the code shown by the claimant at the desk -> item marked returned."""
    if not require_admin():
        return err("Admin only", 403)
    code = ((request.get_json(silent=True) or {}).get("claim_code") or "").strip().upper()
    with get_conn() as conn:
        claim = conn.execute("SELECT * FROM claims WHERE claim_code=?", (code,)).fetchone()
        if not claim:
            return err("Invalid claim code", 404)
        if claim["status"] != "approved":
            return err(f"Claim is {claim['status']}, not approved", 409)
        conn.execute("UPDATE claims SET status='completed' WHERE id=?", (claim["id"],))
        conn.execute("UPDATE listings SET status='returned' WHERE id=?", (claim["listing_id"],))
    return jsonify({"message": "Verified. Item marked as returned.", "listing_id": claim["listing_id"]})


@app.post("/api/admin/listings/<int:listing_id>/return")
def admin_mark_returned(listing_id):
    if not require_admin():
        return err("Admin only", 403)
    with get_conn() as conn:
        cur = conn.execute("UPDATE listings SET status='returned' WHERE id=?", (listing_id,))
    return jsonify({"status": "returned"}) if cur.rowcount else err("Listing not found", 404)


# ---------- stats ----------
@app.get("/api/stats")
def stats():
    with get_conn() as conn:
        r = conn.execute(
            COUNT(*) AS total,
    COALESCE(SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END), 0) AS active,
    COALESCE(SUM(CASE WHEN status = 'found' THEN 1 ELSE 0 END), 0) AS found,
    COALESCE(SUM(CASE WHEN status = 'returned' THEN 1 ELSE 0 END), 0) AS returned
FROM listings
        ).fetchone()
    return jsonify({k: (r[k] or 0) for k in ("total", "active", "found", "returned")})
SELECT
  

@app.post("/api/admin/reset")
def reset_demo():
    if not require_admin():
        return err("Admin only", 403)
    with get_conn() as conn:
        conn.execute("DELETE FROM claims")
        conn.execute("DELETE FROM listings")
    return jsonify({"message": "Demo data cleared"})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
