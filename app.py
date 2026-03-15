import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "steeleops.db")


def get_db():
    if DATABASE_URL.startswith("postgres"):
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn
    else:
        conn = sqlite3.connect(DATABASE_URL)
        conn.row_factory = sqlite3.Row
        return conn


def fetch_count(cursor):
    row = cursor.fetchone()
    if not row:
        return 0

    if isinstance(row, dict):
        return list(row.values())[0]

    if hasattr(row, "keys"):
        return row[row.keys()[0]]

    return row[0]


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS guards (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            company_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id SERIAL PRIMARY KEY,
            guard_id INTEGER,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            location TEXT
        )
    """)

    cur.execute("SELECT COUNT(*) AS cnt FROM companies")
    count = fetch_count(cur)

    if count == 0:
        cur.execute("INSERT INTO companies (name) VALUES (%s)", ("Steele Security Services",))

    conn.commit()
    conn.close()


@app.route("/")
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM guards")
    guards = fetch_count(cur)

    cur.execute("SELECT COUNT(*) AS cnt FROM shifts")
    shifts = fetch_count(cur)

    conn.close()

    return jsonify({
        "platform": "SteeleOps",
        "guards": guards,
        "shifts": shifts,
        "status": "running"
    })


@app.route("/guards", methods=["GET", "POST"])
def manage_guards():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name")
        company_id = request.form.get("company_id")

        cur.execute(
            "INSERT INTO guards (name, company_id) VALUES (%s, %s)",
            (name, company_id)
        )
        conn.commit()
        return redirect(url_for("manage_guards"))

    cur.execute("SELECT * FROM guards")
    guards = cur.fetchall()

    conn.close()

    return jsonify([dict(g) for g in guards])


@app.route("/shifts", methods=["GET", "POST"])
def manage_shifts():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        guard_id = request.form.get("guard_id")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
        location = request.form.get("location")

        cur.execute(
            "INSERT INTO shifts (guard_id, start_time, end_time, location) VALUES (%s, %s, %s, %s)",
            (guard_id, start_time, end_time, location)
        )
        conn.commit()
        return redirect(url_for("manage_shifts"))

    cur.execute("SELECT * FROM shifts")
    shifts = cur.fetchall()

    conn.close()

    return jsonify([dict(s) for s in shifts])


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
