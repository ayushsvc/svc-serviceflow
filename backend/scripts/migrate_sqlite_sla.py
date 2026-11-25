import sqlite3

def run():
    conn = sqlite3.connect("svc_local.db")
    cursor = conn.cursor()

    def add_col_if_missing(table, col, col_type):
        cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            print(f"Added {table}.{col}")
        else:
            print(f"Column {table}.{col} already exists")

    add_col_if_missing("tickets", "priority_code", "TEXT")
    add_col_if_missing("tickets", "sla_effective_start_at", "DATETIME")
    add_col_if_missing("tickets", "first_responded_at", "DATETIME")
    add_col_if_missing("tickets", "sla_matched_tier", "TEXT")
    add_col_if_missing("tickets", "sla_matched_reason", "TEXT")
    add_col_if_missing("tickets", "warn1_triggered", "BOOLEAN DEFAULT 0")
    add_col_if_missing("tickets", "warn2_triggered", "BOOLEAN DEFAULT 0")
    add_col_if_missing("tickets", "breach_triggered", "BOOLEAN DEFAULT 0")
    add_col_if_missing("tickets", "response_breach_triggered", "BOOLEAN DEFAULT 0")
    add_col_if_missing("tickets", "breach_code", "TEXT")
    add_col_if_missing("tickets", "breach_reason", "TEXT")

    add_col_if_missing("slas", "calendar_id", "INTEGER")
    add_col_if_missing("slas", "priorities_config", "JSON DEFAULT '{}'")
    add_col_if_missing("slas", "warning1_pct", "INTEGER DEFAULT 50")
    add_col_if_missing("slas", "warning2_pct", "INTEGER DEFAULT 75")
    add_col_if_missing("slas", "auto_escalate_on_breach", "BOOLEAN DEFAULT 1")
    add_col_if_missing("slas", "require_breach_code", "BOOLEAN DEFAULT 1")
    add_col_if_missing("slas", "reset_response_on_user_reply", "BOOLEAN DEFAULT 0")
    add_col_if_missing("slas", "extend_to_eod", "BOOLEAN DEFAULT 0")

    conn.commit()
    conn.close()
    print("Schema Migration for SQLite OK")

if __name__ == "__main__":
    run()
