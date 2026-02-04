"""
Supabase persistence layer for Product Description Generator.

Stores batch run history and individual product descriptions,
enabling cross-run apply, rollback, and audit trails.

Uses the PostgREST API directly via requests (no extra dependencies).
"""

import os
import json
import requests
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def is_configured():
    """Check if Supabase credentials are available."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers():
    """Standard headers for Supabase PostgREST calls."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _api(path):
    """Build full URL for a PostgREST endpoint."""
    return f"{SUPABASE_URL}/rest/v1/{path}"


# ---------------------------------------------------------------------------
# Batch runs
# ---------------------------------------------------------------------------

def create_run(run_id, model_provider, model_name, content_source, product_filter, total_products):
    """Insert a new batch run record. Returns the created row or None."""
    payload = {
        "run_id": run_id,
        "model_provider": model_provider,
        "model_name": model_name,
        "content_source": content_source,
        "product_filter": product_filter,
        "total_products": total_products,
        "status": "generating",
    }
    resp = requests.post(_api("batch_runs"), headers=_headers(), json=payload)
    if resp.status_code in (200, 201):
        rows = resp.json()
        return rows[0] if rows else None
    print(f"  Supabase: failed to create run ({resp.status_code}): {resp.text[:200]}")
    return None


def update_run(run_id, **fields):
    """Patch fields on an existing run."""
    headers = _headers()
    headers["Prefer"] = "return=minimal"
    resp = requests.patch(
        _api(f"batch_runs?run_id=eq.{run_id}"),
        headers=headers,
        json=fields,
    )
    return resp.status_code in (200, 204)


def get_run(run_id):
    """Fetch a single run by run_id."""
    resp = requests.get(
        _api(f"batch_runs?run_id=eq.{run_id}&limit=1"),
        headers=_headers(),
    )
    if resp.status_code == 200:
        rows = resp.json()
        return rows[0] if rows else None
    return None


def list_runs(limit=10):
    """List recent runs, newest first."""
    resp = requests.get(
        _api(f"batch_runs?order=created_at.desc&limit={limit}"),
        headers=_headers(),
    )
    return resp.json() if resp.status_code == 200 else []


# ---------------------------------------------------------------------------
# Product descriptions
# ---------------------------------------------------------------------------

def save_description(run_id, shopify_product_id, product_title,
                     body_html_before, body_html_after, word_count,
                     status="generated", error_message=None):
    """Insert a single product description record."""
    payload = {
        "run_id": run_id,
        "shopify_product_id": shopify_product_id,
        "product_title": product_title,
        "body_html_before": body_html_before,
        "body_html_after": body_html_after,
        "word_count": word_count,
        "status": status,
        "error_message": error_message,
    }
    resp = requests.post(_api("product_descriptions"), headers=_headers(), json=payload)
    return resp.status_code in (200, 201)


def save_descriptions_batch(rows):
    """Insert multiple description records in one call."""
    if not rows:
        return True
    resp = requests.post(_api("product_descriptions"), headers=_headers(), json=rows)
    if resp.status_code in (200, 201):
        return True
    print(f"  Supabase: batch insert failed ({resp.status_code}): {resp.text[:300]}")
    return False


def get_descriptions(run_id, status=None):
    """Fetch all descriptions for a run, optionally filtered by status."""
    query = f"product_descriptions?run_id=eq.{run_id}&order=created_at.asc"
    if status:
        query += f"&status=eq.{status}"
    resp = requests.get(_api(query), headers=_headers())
    return resp.json() if resp.status_code == 200 else []


def mark_applied(run_id, shopify_product_id):
    """Mark a single product description as applied to Shopify."""
    now = datetime.now(timezone.utc).isoformat()
    headers = _headers()
    headers["Prefer"] = "return=minimal"
    resp = requests.patch(
        _api(f"product_descriptions?run_id=eq.{run_id}&shopify_product_id=eq.{shopify_product_id}"),
        headers=headers,
        json={"status": "applied", "applied_at": now},
    )
    return resp.status_code in (200, 204)


def mark_rolled_back(run_id, shopify_product_id):
    """Mark a single product description as rolled back."""
    headers = _headers()
    headers["Prefer"] = "return=minimal"
    resp = requests.patch(
        _api(f"product_descriptions?run_id=eq.{run_id}&shopify_product_id=eq.{shopify_product_id}"),
        headers=headers,
        json={"status": "rolled_back"},
    )
    return resp.status_code in (200, 204)


def get_latest_run_id():
    """Get the run_id of the most recent completed run."""
    resp = requests.get(
        _api("batch_runs?status=in.(generated,applied)&order=created_at.desc&limit=1"),
        headers=_headers(),
    )
    if resp.status_code == 200:
        rows = resp.json()
        return rows[0]["run_id"] if rows else None
    return None
