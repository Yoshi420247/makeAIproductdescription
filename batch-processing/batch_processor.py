#!/usr/bin/env python3
"""
Parallel Product Description Generator using OpenAI Batch API

Process hundreds or thousands of Shopify products simultaneously.
50% cheaper than synchronous API calls, no rate limits.

Usage:
    python batch_processor.py submit    # Create batch from Shopify products
    python batch_processor.py status    # Check batch status
    python batch_processor.py download  # Download and apply results

Environment Variables Required:
    OPENAI_API_KEY        - Your OpenAI API key
    SHOPIFY_STORE         - Your store (e.g., oil-slick-pad.myshopify.com)
    SHOPIFY_ACCESS_TOKEN  - Shopify Admin API access token

Optional Filter Variables:
    PRODUCT_FILTER        - all_active, all_products, needs_description, short_description, by_type, by_vendor, by_tag
    FILTER_PRODUCT_TYPE   - Filter by product type (when PRODUCT_FILTER=by_type)
    FILTER_VENDOR         - Filter by vendor (when PRODUCT_FILTER=by_vendor)
    FILTER_TAG            - Filter by tag (when PRODUCT_FILTER=by_tag)
    INCLUDE_DRAFTS        - Include draft products (true/false)
    MIN_DESCRIPTION_LENGTH - Minimum chars to consider "has description" (default: 100)
"""

import os
import json
import time
import argparse
import requests
from pathlib import Path

# === CONFIGURATION ===
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")

# Filter options
PRODUCT_FILTER = os.environ.get("PRODUCT_FILTER", "all_active")
FILTER_PRODUCT_TYPE = os.environ.get("FILTER_PRODUCT_TYPE", "")
FILTER_VENDOR = os.environ.get("FILTER_VENDOR", "")
FILTER_TAG = os.environ.get("FILTER_TAG", "")
INCLUDE_DRAFTS = os.environ.get("INCLUDE_DRAFTS", "false").lower() == "true"
MIN_DESCRIPTION_LENGTH = int(os.environ.get("MIN_DESCRIPTION_LENGTH", "100"))

BATCH_FILE = "batch_requests.jsonl"
BATCH_ID_FILE = "batch_id.txt"
RESULTS_FILE = "batch_results.jsonl"

# === SYSTEM PROMPT (GPT-5.1 Optimized) ===
SYSTEM_PROMPT = """You are an expert e-commerce copywriter for Oil Slick. Write product descriptions that sound genuinely human and helpful.

# OUTPUT FORMAT (STRICT)
Return ONLY valid JSON: {"ai_body_html": "<h2>Overview</h2><p>...</p>..."}

# VOICE RULES
- Write like explaining to a friend, not a robot
- Vary sentence length naturally. Short sometimes. Longer when explaining.
- Use contractions: you'll, it's, doesn't
- NO buzzwords: "elevate," "unlock," "game-changer," "revolutionize"
- NO phrases: "dive into," "it's important to note," "when it comes to"
- NO hollow superlatives without specifics

# CONTENT STRUCTURE
- <h2>Overview</h2> (50-80 words): What it IS, who it's FOR, primary benefit
- <h2>Key Features</h2> — Benefit-focused bullets, 5-8 max
- <h2>Specs & Compatibility</h2> — Only with real data, tables for 3+ specs
- <h2>Common Questions</h2> — 3-5 FAQs phrased how shoppers ask (GEO-critical)

# CATEGORY INTELLIGENCE
**Downstems**: Define "effective length" (shoulder to tip). Joint size (10/14/18mm), angle (45°/90°), gender.
**Bongs**: Perc type explained plainly. Joint specs. Cleaning notes.
**Grinders**: Piece count, kief catch, tooth design, material.
**Dab Rigs**: Banger compatibility, joint size, recycler function.
**Hand Pipes**: Bowl size, carb placement, portability.
**Vaporizers**: Heating method, temp control, battery life.

# RULES
- Length: 1,200-1,600 words. Quality over padding.
- No fabricated specs. Only use provided data.
- HTML tags: h2, h3, p, ul, ol, li, strong, em, a, table
- Dual units: "4.5 inches (114mm)"
"""


def check_env():
    """Verify all required environment variables are set."""
    missing = []
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not SHOPIFY_STORE:
        missing.append("SHOPIFY_STORE")
    if not SHOPIFY_ACCESS_TOKEN:
        missing.append("SHOPIFY_ACCESS_TOKEN")

    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("\nSet them in your environment or GitHub Secrets:")
        print("  OPENAI_API_KEY=sk-...")
        print("  SHOPIFY_STORE=your-store.myshopify.com")
        print("  SHOPIFY_ACCESS_TOKEN=shpat_...")
        exit(1)


def get_shopify_products():
    """Fetch products from Shopify based on filter settings."""

    print(f"\n📋 FILTER SETTINGS:")
    print(f"   Filter: {PRODUCT_FILTER}")
    if FILTER_PRODUCT_TYPE:
        print(f"   Product Type: {FILTER_PRODUCT_TYPE}")
    if FILTER_VENDOR:
        print(f"   Vendor: {FILTER_VENDOR}")
    if FILTER_TAG:
        print(f"   Tag: {FILTER_TAG}")
    print(f"   Include Drafts: {INCLUDE_DRAFTS}")
    print(f"   Min Description Length: {MIN_DESCRIPTION_LENGTH}")
    print()

    # Build API parameters
    params = {"limit": 250}

    # Status filter
    if PRODUCT_FILTER == "all_products" or INCLUDE_DRAFTS:
        # Don't filter by status - get everything
        pass
    else:
        params["status"] = "active"

    # Type filter (API level)
    if PRODUCT_FILTER == "by_type" and FILTER_PRODUCT_TYPE:
        params["product_type"] = FILTER_PRODUCT_TYPE

    # Vendor filter (API level)
    if PRODUCT_FILTER == "by_vendor" and FILTER_VENDOR:
        params["vendor"] = FILTER_VENDOR

    url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}

    all_products = []
    page = 1

    while url:
        print(f"  Fetching page {page}...")
        response = requests.get(url, headers=headers, params=params)

        if response.status_code != 200:
            print(f"❌ Shopify API error: {response.status_code}")
            print(response.text)
            exit(1)

        data = response.json()
        products = data.get("products", [])
        all_products.extend(products)
        print(f"  Got {len(products)} products (total: {len(all_products)})")

        # Handle pagination via Link header
        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    url = link.split(";")[0].strip("<> ")
                    page += 1
        params = {}  # Clear params for paginated requests

    print(f"✓ Fetched {len(all_products)} products from Shopify")

    # Apply additional filters
    filtered_products = apply_filters(all_products)

    return filtered_products


def apply_filters(products):
    """Apply additional filters that can't be done at API level."""

    original_count = len(products)
    filtered = products

    # Filter by tag
    if PRODUCT_FILTER == "by_tag" and FILTER_TAG:
        tag_lower = FILTER_TAG.lower()
        filtered = [
            p for p in filtered
            if tag_lower in (p.get("tags", "") or "").lower()
        ]
        print(f"  Filtered by tag '{FILTER_TAG}': {len(filtered)} products")

    # Filter: needs description (empty or very short)
    if PRODUCT_FILTER == "needs_description":
        filtered = [
            p for p in filtered
            if not p.get("body_html") or len(p.get("body_html", "").strip()) < MIN_DESCRIPTION_LENGTH
        ]
        print(f"  Filtered to products needing descriptions: {len(filtered)} products")

    # Filter: short description (has some content but below threshold)
    if PRODUCT_FILTER == "short_description":
        filtered = [
            p for p in filtered
            if p.get("body_html") and 0 < len(p.get("body_html", "").strip()) < MIN_DESCRIPTION_LENGTH
        ]
        print(f"  Filtered to products with short descriptions: {len(filtered)} products")

    # Exclude drafts if not included
    if not INCLUDE_DRAFTS and PRODUCT_FILTER != "all_products":
        filtered = [p for p in filtered if p.get("status") != "draft"]

    if len(filtered) != original_count:
        print(f"  Final count after filters: {len(filtered)} products (from {original_count})")

    return filtered


def create_batch_request(product):
    """Create a single batch request for a product."""
    # Safely extract product data
    title = product.get('title', 'Unknown Product')
    product_type = product.get('product_type', '')
    tags = product.get('tags', '')
    body_html = product.get('body_html', '') or ''
    vendor = product.get('vendor', '')

    # Truncate long descriptions
    if len(body_html) > 1000:
        body_html = body_html[:1000] + "..."

    # Format options and variants
    options = product.get('options', [])
    options_str = ", ".join([o.get('name', '') for o in options]) if options else "None"

    variants = product.get('variants', [])
    variants_summary = []
    for v in variants[:5]:  # Limit to first 5 variants
        variants_summary.append(f"{v.get('title', '')}: ${v.get('price', '0')}")
    variants_str = "; ".join(variants_summary) if variants_summary else "Single variant"

    user_content = f"""Write a product description for:

Title: {title}
Vendor: {vendor if vendor else 'Oil Slick'}
Type: {product_type}
Tags: {tags}
Options: {options_str}
Variants: {variants_str}
Current Description: {body_html if body_html else '(none)'}

Return JSON only: {{"ai_body_html": "..."}}"""

    return {
        "custom_id": str(product["id"]),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": "gpt-5.1",
            "response_format": {"type": "json_object"},
            "max_tokens": 16000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ]
        }
    }


def submit_batch():
    """Create and submit a batch job to OpenAI."""
    check_env()

    print("\n📦 STEP 1: Fetching products from Shopify...")
    products = get_shopify_products()

    if not products:
        print("❌ No products found matching your filters!")
        print("\nTry adjusting your filter settings:")
        print("  - PRODUCT_FILTER: all_active, all_products, needs_description, etc.")
        print("  - INCLUDE_DRAFTS: true/false")
        return

    print(f"\n📝 STEP 2: Creating batch file with {len(products)} requests...")
    with open(BATCH_FILE, "w") as f:
        for i, product in enumerate(products):
            if i % 50 == 0:
                print(f"  Processing product {i+1}/{len(products)}...")
            request = create_batch_request(product)
            f.write(json.dumps(request) + "\n")

    file_size = os.path.getsize(BATCH_FILE) / 1024 / 1024
    print(f"✓ Created {BATCH_FILE} ({file_size:.2f} MB)")

    print("\n📤 STEP 3: Uploading batch file to OpenAI...")
    with open(BATCH_FILE, "rb") as f:
        response = requests.post(
            "https://api.openai.com/v1/files",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("batch_requests.jsonl", f, "application/jsonl")},
            data={"purpose": "batch"}
        )

    if response.status_code != 200:
        print(f"❌ Failed to upload file: {response.status_code}")
        print(response.text)
        exit(1)

    file_id = response.json()["id"]
    print(f"✓ File uploaded: {file_id}")

    print("\n🚀 STEP 4: Creating batch job...")
    response = requests.post(
        "https://api.openai.com/v1/batches",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "input_file_id": file_id,
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h"
        }
    )

    if response.status_code != 200:
        print(f"❌ Failed to create batch: {response.status_code}")
        print(response.text)
        exit(1)

    batch_data = response.json()
    batch_id = batch_data["id"]

    # Save batch ID
    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch_id)

    # Estimate cost (rough: ~$0.025 per product at ~2K tokens output)
    estimated_cost = len(products) * 0.025

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ BATCH SUBMITTED                          ║
╠══════════════════════════════════════════════════════════════════╣
║  Batch ID:      {batch_id:<48} ║
║  Products:      {len(products):<48} ║
║  Filter:        {PRODUCT_FILTER:<48} ║
║  Est. Cost:     ~${estimated_cost:.2f} (50% off regular API){' ':<21} ║
║  Status:        {batch_data.get('status', 'unknown'):<48} ║
╠══════════════════════════════════════════════════════════════════╣
║  NEXT STEPS:                                                     ║
║  1. Wait 1-4 hours for processing                                ║
║  2. Run with action: status                                      ║
║  3. When complete, run with action: download                     ║
╚══════════════════════════════════════════════════════════════════╝
""")

    # Print batch ID prominently for easy copying
    print(f"BATCH_ID: {batch_id}")


def check_status():
    """Check the status of the current batch job."""
    check_env()

    if not Path(BATCH_ID_FILE).exists():
        print("❌ No batch ID found. Run with action 'submit' first.")
        return None

    with open(BATCH_ID_FILE) as f:
        batch_id = f.read().strip()

    print(f"🔍 Checking batch: {batch_id}")

    response = requests.get(
        f"https://api.openai.com/v1/batches/{batch_id}",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )

    if response.status_code != 200:
        print(f"❌ Failed to get batch status: {response.status_code}")
        print(response.text)
        return None

    batch = response.json()

    # Calculate progress
    counts = batch.get("request_counts", {})
    total = counts.get("total", 0)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    progress = (completed / total * 100) if total > 0 else 0

    status_emoji = {
        "validating": "🔄",
        "in_progress": "⏳",
        "finalizing": "📦",
        "completed": "✅",
        "failed": "❌",
        "expired": "⚠️",
        "cancelled": "🚫"
    }.get(batch.get("status", ""), "❓")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                       BATCH STATUS                               ║
╠══════════════════════════════════════════════════════════════════╣
║  Batch ID:    {batch_id:<50} ║
║  Status:      {status_emoji} {batch.get('status', 'unknown'):<48} ║
║  Progress:    {completed}/{total} requests ({progress:.1f}% complete){' ':<21} ║
║  Failed:      {failed:<50} ║""")

    if batch.get("status") == "completed":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  Output File: {batch.get('output_file_id', 'N/A'):<50} ║
╠══════════════════════════════════════════════════════════════════╣
║  ✅ READY! Run with action: download                             ║
╚══════════════════════════════════════════════════════════════════╝
""")
    elif batch.get("status") == "in_progress":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ⏳ Still processing... Check again in a few minutes.            ║
╚══════════════════════════════════════════════════════════════════╝
""")
    elif batch.get("status") == "failed":
        errors = batch.get("errors", {}).get("data", [])
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ❌ BATCH FAILED                                                  ║""")
        for err in errors[:3]:
            print(f"║  Error: {str(err)[:56]:<56} ║")
        print(f"""╚══════════════════════════════════════════════════════════════════╝
""")
    else:
        print(f"""╚══════════════════════════════════════════════════════════════════╝
""")

    return batch


def download_and_apply():
    """Download batch results and update Shopify products."""
    check_env()

    if not Path(BATCH_ID_FILE).exists():
        print("❌ No batch ID found. Run 'submit' first.")
        return

    with open(BATCH_ID_FILE) as f:
        batch_id = f.read().strip()

    # Check status first
    print("🔍 Checking batch status...")
    response = requests.get(
        f"https://api.openai.com/v1/batches/{batch_id}",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )

    batch = response.json()

    if batch.get("status") != "completed":
        print(f"❌ Batch not ready. Status: {batch.get('status')}")
        print("Run with action 'status' to check progress.")
        return

    output_file_id = batch.get("output_file_id")
    if not output_file_id:
        print("❌ No output file found in batch.")
        return

    # Download results
    print(f"\n📥 Downloading results from {output_file_id}...")
    response = requests.get(
        f"https://api.openai.com/v1/files/{output_file_id}/content",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )

    if response.status_code != 200:
        print(f"❌ Failed to download results: {response.status_code}")
        return

    with open(RESULTS_FILE, "wb") as f:
        f.write(response.content)

    print(f"✓ Downloaded results to {RESULTS_FILE}")

    # Parse and apply results
    print("\n🔄 Applying results to Shopify...")

    success_count = 0
    error_count = 0
    skip_count = 0

    with open(RESULTS_FILE) as f:
        lines = f.readlines()
        total = len(lines)

        for i, line in enumerate(lines):
            try:
                result = json.loads(line)
                product_id = result.get("custom_id")

                if (i + 1) % 10 == 0 or i == 0:
                    print(f"  Processing {i+1}/{total}...")

                # Check for errors in the response
                if result.get("error"):
                    print(f"  ⚠️ API error for {product_id}: {result['error']}")
                    error_count += 1
                    continue

                # Extract AI-generated HTML
                response_body = result.get("response", {}).get("body", {})
                choices = response_body.get("choices", [])

                if not choices:
                    print(f"  ⚠️ No choices in response for {product_id}")
                    error_count += 1
                    continue

                content = choices[0].get("message", {}).get("content", "")

                try:
                    parsed = json.loads(content)
                    body_html = parsed.get("ai_body_html", "")
                except json.JSONDecodeError:
                    print(f"  ⚠️ Invalid JSON response for {product_id}")
                    error_count += 1
                    continue

                if len(body_html) < 100:
                    print(f"  ⚠️ Skipping {product_id}: Response too short")
                    skip_count += 1
                    continue

                # Update Shopify
                url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products/{product_id}.json"
                headers = {
                    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
                    "Content-Type": "application/json"
                }
                payload = {"product": {"id": int(product_id), "body_html": body_html}}

                response = requests.put(url, headers=headers, json=payload)

                if response.status_code == 200:
                    success_count += 1
                else:
                    error_count += 1
                    print(f"  ✗ Failed {product_id}: {response.status_code}")

                # Rate limiting for Shopify API (2 requests/second limit)
                time.sleep(0.5)

            except Exception as e:
                error_count += 1
                print(f"  ✗ Error: {e}")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ COMPLETE                                 ║
╠══════════════════════════════════════════════════════════════════╣
║  ✓ Successfully updated:  {success_count:<42} ║
║  ⚠️ Skipped (too short):   {skip_count:<42} ║
║  ✗ Errors:                {error_count:<42} ║
╠══════════════════════════════════════════════════════════════════╣
║  Your Shopify products have been updated with AI descriptions!   ║
╚══════════════════════════════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser(
        description="Parallel Product Description Generator using OpenAI Batch API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python batch_processor.py submit     # Create batch from Shopify products
  python batch_processor.py status     # Check batch status
  python batch_processor.py download   # Download results and update Shopify

Filter Environment Variables:
  PRODUCT_FILTER          all_active, all_products, needs_description,
                          short_description, by_type, by_vendor, by_tag
  FILTER_PRODUCT_TYPE     Product type to filter by
  FILTER_VENDOR           Vendor name to filter by
  FILTER_TAG              Tag to filter by
  INCLUDE_DRAFTS          true/false - include draft products
  MIN_DESCRIPTION_LENGTH  Minimum chars for "has description" (default: 100)
        """
    )
    parser.add_argument(
        "command",
        choices=["submit", "status", "download"],
        help="Command to run: submit, status, or download"
    )
    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           Using OpenAI Batch API (50%% cheaper!)                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    if args.command == "submit":
        submit_batch()
    elif args.command == "status":
        check_status()
    elif args.command == "download":
        download_and_apply()


if __name__ == "__main__":
    main()
