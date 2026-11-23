"""CampusFind REST API (Flask + PostgreSQL on Render, SQLite locally)."""
import os
import re
import secrets
import string
from datetime import date

from flask import Flask, jsonify, request

from db import get_conn, init_db
from matching import find_matches

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # request limit: 2 MB

# Set ALLOWED_ORIGIN on Render to your exact GitHub Pages origin.
ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "ALLOWED_ORIGIN", "https://samridhi16-ai.github.io"
    ).split(",")
    if origin.strip()
}


@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)


@app.get("/health")
def health():
    """Simple deployment check; also verifies that the database is reachable."""
    with get_conn() as conn:
        conn.execute("SELECT 1").fetchone()
    return jsonify({"ok": True, "service": "CampusFind API"})


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin123")
CATEGORIES = {
    "Electronics", "Keys", "Cards & ID", "Bags", "Books & Notes",
    "Clothing", "Accessories", "Other",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_PHOTO_CHARS = 1_500_000


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
    """Never expose the reporter's email in public listing responses."""
    data = dict(row)
    data.pop("email", None)
    return data


@app.get("/api/listings")
def list_listings():
    query = (request.args.get("q") or "").strip().lower()
    item_type = request.args.get("type")
    status = request.args.get("status")
    category = request.args.get("category")
    order = "ASC" if request.args.get("sort") == "oldest" else "DESC"

    sql, params = "SELECT * FROM listings WHERE 1=1", []
    if item_type in ("lost", "found"):
        sql += " AND type=?"
        params.append(item_type)
    if status in ("active", "returned"):
        sql += " AND status=?"
        params.append(status)
    if category and category.lower() != "all":
        sql += " AND category=?"
        params.append(category)
    if query:
        sql += " AND (lower(name) LIKE ? OR lower(description) LIKE ? OR lower(location) LIKE ?)"
        params.extend([f"%{query}%"] * 3)
    sql += f" ORDER BY created_at {order}, id {order}"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify([public_listing(row) for row in rows])


@app.post("/api/listings")
def create_listing():
    data = request.get_json(silent=True) or {}
    required = ["type", "category", "name", "description", "location", "date", "reporter", "email"]
    missing = [field for field in required if not str(data.get(field, "")).strip()]
    if missing:
        return err(f"Missing fields: {', '.join(missing)}")
    if data["type"] not in ("lost", "found"):
        return err("type must be 'lost' or 'found'")
    if data["category"] not in CATEGORIES:
        return err("Unknown category")
    if not EMAIL_RE.match(data["email"].strip()):
        return err("Enter a valid email address")
    try:
        if date.fromisoformat(data["date"]) > date.today():
            return err("Date cannot be in the future")
    except ValueError:
        return err("Date must be YYYY-MM-DD")

    photo = data.get("photo") or None
    if photo and (not isinstance(photo, str) or len(photo) > MAX_PHOTO_CHARS):
        return err("Photo too large (max approximately 1 MB)")

    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO listings
               (type, category, name, description, location, date, reporter, email, photo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (data["type"], data["category"], data["name"].strip(),
             data["description"].strip(), data["location"].strip(), data["date"],
             data["reporter"].strip(), data["email"].strip().lower(), photo),
        )
        listing_id = cursor.fetchone()["id"]
        row = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        all_rows = [dict(item) for item in conn.execute("SELECT * FROM listings").fetchall()]

    matches = find_matches(dict(row), all_rows)
    return jsonify({
        "listing": public_listing(row),
        "suggested_matches": [
            {"score": match["score"], "listing": public_listing(match["listing"])}
            for match in matches
        ],
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
        all_rows = [dict(item) for item in conn.execute("SELECT * FROM listings").fetchall()]
    matches = find_matches(dict(row), all_rows)
    return jsonify([
        {"score": match["score"], "listing": public_listing(match["listing"])}
        for match in matches
    ])


@app.delete("/api/listings/<int:listing_id>")
def delete_listing(listing_id):
    if not require_admin():
        return err("Admin only", 403)
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM listings WHERE id=?", (listing_id,))
    return ("", 204) if cursor.rowcount else err("Listing not found", 404)


@app.post("/api/listings/<int:listing_id>/claims")
def submit_claim(listing_id):
    data = request.get_json(silent=True) or {}
    for field in ("claimant", "email", "proof"):
        if not str(data.get(field, "")).strip():
            return err(f"Missing field: {field}")
    if not EMAIL_RE.match(data["email"].strip()):
        return err("Enter a valid email address")

    with get_conn() as conn:
        listing = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not listing:
            return err("Listing not found", 404)
        if listing["status"] != "active":
            return err("This item has already been returned", 409)
        code = new_claim_code(conn)
        conn.execute(
            "INSERT INTO claims (listing_id, claimant, email, proof, claim_code) VALUES (?, ?, ?, ?, ?)",
            (listing_id, data["claimant"].strip(), data["email"].strip().lower(),
             data["proof"].strip(), code),
        )
    return jsonify({
        "claim_code": code,
        "status": "pending",
        "message": "Claim sent. Show this code at the campus office once approved.",
    }), 201


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
    return jsonify([dict(row) for row in rows])


@app.post("/api/admin/claims/<int:claim_id>/<action>")
def admin_review_claim(claim_id, action):
    if not require_admin():
        return err("Admin only", 403)
    if action not in ("approve", "reject"):
        return err("Action must be approve or reject")
    status = "approved" if action == "approve" else "rejected"
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE claims SET status=? WHERE id=? AND status='pending'",
            (status, claim_id),
        )
    return jsonify({"status": status}) if cursor.rowcount else err("Claim not found or already reviewed", 404)


@app.post("/api/admin/verify-code")
def admin_verify_code():
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
        cursor = conn.execute("UPDATE listings SET status='returned' WHERE id=?", (listing_id,))
    return jsonify({"status": "returned"}) if cursor.rowcount else err("Listing not found", 404)


@app.get("/api/stats")
def stats():
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
                      SUM(CASE WHEN type='found' THEN 1 ELSE 0 END) AS found,
                      SUM(CASE WHEN status='returned' THEN 1 ELSE 0 END) AS returned
               FROM listings"""
        ).fetchone()
    return jsonify({key: (row[key] or 0) for key in ("total", "active", "found", "returned")})


@app.post("/api/admin/reset")
def reset_demo():
    if not require_admin():
        return err("Admin only", 403)
    with get_conn() as conn:
        conn.execute("DELETE FROM claims")
        conn.execute("DELETE FROM listings")
    return jsonify({"message": "Demo data cleared"})


# Initialize tables when Gunicorn imports this module on Render.
init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
