#!/usr/bin/env python3
"""
Parallel Product Description Generator using OpenAI Batch API

Process hundreds or thousands of Shopify products simultaneously.
50% cheaper than synchronous API calls, no rate limits.

Usage:
    python batch_processor.py submit    # Create batch from Shopify products
    python batch_processor.py status    # Check batch status
    python batch_processor.py preview   # Preview descriptions WITHOUT updating Shopify
    python batch_processor.py download  # Download and apply to Shopify

Environment Variables Required:
    OPENAI_API_KEY        - Your OpenAI API key
    SHOPIFY_STORE         - Your store (e.g., oil-slick-pad.myshopify.com)
    SHOPIFY_ACCESS_TOKEN  - Shopify Admin API access token
"""

import os
import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime
from html import escape

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

# Preview/dry-run options
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
SAMPLE_COUNT = int(os.environ.get("SAMPLE_COUNT", "5"))

# Batch ID (can be passed directly via env var)
BATCH_ID_INPUT = os.environ.get("BATCH_ID", "").strip()

BATCH_FILE = "batch_requests.jsonl"
BATCH_ID_FILE = "batch_id.txt"
RESULTS_FILE = "batch_results.jsonl"
PREVIEW_FILE = "preview_report.html"


def get_batch_id():
    """Get batch ID from environment variable or file."""
    if BATCH_ID_INPUT:
        print(f"📋 Using batch ID from input: {BATCH_ID_INPUT}")
        return BATCH_ID_INPUT

    if Path(BATCH_ID_FILE).exists():
        with open(BATCH_ID_FILE) as f:
            batch_id = f.read().strip()
            print(f"📋 Using batch ID from file: {batch_id}")
            return batch_id

    return None

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
    print()

    params = {"limit": 250}
    if PRODUCT_FILTER != "all_products" and not INCLUDE_DRAFTS:
        params["status"] = "active"
    if PRODUCT_FILTER == "by_type" and FILTER_PRODUCT_TYPE:
        params["product_type"] = FILTER_PRODUCT_TYPE
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

        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    url = link.split(";")[0].strip("<> ")
                    page += 1
        params = {}

    print(f"✓ Fetched {len(all_products)} products from Shopify")
    return apply_filters(all_products)


def apply_filters(products):
    """Apply additional filters."""
    filtered = products

    if PRODUCT_FILTER == "by_tag" and FILTER_TAG:
        tag_lower = FILTER_TAG.lower()
        filtered = [p for p in filtered if tag_lower in (p.get("tags", "") or "").lower()]
        print(f"  Filtered by tag '{FILTER_TAG}': {len(filtered)} products")

    if PRODUCT_FILTER == "needs_description":
        filtered = [p for p in filtered if not p.get("body_html") or len(p.get("body_html", "").strip()) < MIN_DESCRIPTION_LENGTH]
        print(f"  Filtered to products needing descriptions: {len(filtered)} products")

    if PRODUCT_FILTER == "short_description":
        filtered = [p for p in filtered if p.get("body_html") and 0 < len(p.get("body_html", "").strip()) < MIN_DESCRIPTION_LENGTH]
        print(f"  Filtered to short descriptions: {len(filtered)} products")

    if not INCLUDE_DRAFTS and PRODUCT_FILTER != "all_products":
        filtered = [p for p in filtered if p.get("status") != "draft"]

    return filtered


def create_batch_request(product):
    """Create a single batch request for a product."""
    title = product.get('title', 'Unknown Product')
    product_type = product.get('product_type', '')
    tags = product.get('tags', '')
    body_html = product.get('body_html', '') or ''
    vendor = product.get('vendor', '')

    if len(body_html) > 1000:
        body_html = body_html[:1000] + "..."

    options = product.get('options', [])
    options_str = ", ".join([o.get('name', '') for o in options]) if options else "None"

    variants = product.get('variants', [])
    variants_summary = [f"{v.get('title', '')}: ${v.get('price', '0')}" for v in variants[:5]]
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
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"input_file_id": file_id, "endpoint": "/v1/chat/completions", "completion_window": "24h"}
    )

    if response.status_code != 200:
        print(f"❌ Failed to create batch: {response.status_code}")
        print(response.text)
        exit(1)

    batch_data = response.json()
    batch_id = batch_data["id"]

    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch_id)

    estimated_cost = len(products) * 0.025

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ BATCH SUBMITTED                          ║
╠══════════════════════════════════════════════════════════════════╣
║  Batch ID:      {batch_id:<48} ║
║  Products:      {len(products):<48} ║
║  Filter:        {PRODUCT_FILTER:<48} ║
║  Est. Cost:     ~${estimated_cost:.2f} (50% off regular API){' ':<21} ║
╠══════════════════════════════════════════════════════════════════╣
║  NEXT STEPS:                                                     ║
║  1. Wait for processing (check with action: status)              ║
║  2. Preview results (action: preview)                            ║
║  3. Apply to Shopify (action: download)                          ║
╚══════════════════════════════════════════════════════════════════╝
""")
    print(f"BATCH_ID: {batch_id}")


def check_status():
    """Check the status of the current batch job."""
    check_env()

    batch_id = get_batch_id()
    if not batch_id:
        print("❌ No batch ID found. Provide batch_id input or run 'submit' first.")
        return None

    print(f"🔍 Checking batch: {batch_id}")

    response = requests.get(
        f"https://api.openai.com/v1/batches/{batch_id}",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )

    if response.status_code != 200:
        print(f"❌ Failed to get batch status: {response.status_code}")
        return None

    batch = response.json()
    counts = batch.get("request_counts", {})
    total = counts.get("total", 0)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    progress = (completed / total * 100) if total > 0 else 0

    status_emoji = {"validating": "🔄", "in_progress": "⏳", "finalizing": "📦", "completed": "✅", "failed": "❌", "expired": "⚠️"}.get(batch.get("status", ""), "❓")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                       BATCH STATUS                               ║
╠══════════════════════════════════════════════════════════════════╣
║  Batch ID:    {batch_id:<50} ║
║  Status:      {status_emoji} {batch.get('status', 'unknown'):<48} ║
║  Progress:    {completed}/{total} ({progress:.1f}% complete){' ':<27} ║
║  Failed:      {failed:<50} ║""")

    if batch.get("status") == "completed":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ✅ READY!                                                        ║
║  → Run 'preview' to see samples before applying                  ║
║  → Run 'download' to apply all to Shopify                        ║
╚══════════════════════════════════════════════════════════════════╝
""")
    elif batch.get("status") == "in_progress":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ⏳ Still processing... Check again in a few minutes.            ║
╚══════════════════════════════════════════════════════════════════╝
""")
    else:
        print(f"""╚══════════════════════════════════════════════════════════════════╝
""")

    return batch


def generate_preview_html(results, sample_count=5):
    """Generate an HTML preview report."""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Product Description Preview</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}
        .header h1 {{ margin: 0 0 10px 0; }}
        .stats {{
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
        }}
        .stat {{
            background: rgba(255,255,255,0.2);
            padding: 10px 20px;
            border-radius: 5px;
        }}
        .product {{
            background: white;
            border-radius: 10px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .product-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #eee;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}
        .product-title {{
            font-size: 1.4em;
            font-weight: bold;
            color: #333;
        }}
        .product-id {{
            color: #888;
            font-size: 0.9em;
        }}
        .description {{
            line-height: 1.6;
            color: #444;
        }}
        .description h2 {{
            color: #667eea;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
        }}
        .description h3 {{
            color: #764ba2;
        }}
        .description ul, .description ol {{
            padding-left: 25px;
        }}
        .description li {{
            margin-bottom: 8px;
        }}
        .description table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        .description th, .description td {{
            border: 1px solid #ddd;
            padding: 10px;
            text-align: left;
        }}
        .description th {{
            background: #f8f9fa;
        }}
        .word-count {{
            background: #e8f5e9;
            color: #2e7d32;
            padding: 5px 12px;
            border-radius: 15px;
            font-size: 0.85em;
        }}
        .error {{
            background: #ffebee;
            color: #c62828;
            padding: 15px;
            border-radius: 5px;
        }}
        .footer {{
            text-align: center;
            color: #888;
            padding: 30px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🔍 Product Description Preview</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <div class="stats">
            <div class="stat">📦 Total Products: {len(results)}</div>
            <div class="stat">👀 Showing: {min(sample_count, len(results))} samples</div>
        </div>
    </div>
"""

    shown = 0
    for result in results:
        if shown >= sample_count:
            break

        product_id = result.get("custom_id", "Unknown")

        # Check for errors
        if result.get("error"):
            html += f"""
    <div class="product">
        <div class="product-header">
            <span class="product-title">Product ID: {product_id}</span>
        </div>
        <div class="error">❌ Error: {escape(str(result.get('error')))}</div>
    </div>
"""
            shown += 1
            continue

        # Extract content
        try:
            response_body = result.get("response", {}).get("body", {})
            choices = response_body.get("choices", [])
            if not choices:
                continue
            content = choices[0].get("message", {}).get("content", "")
            parsed = json.loads(content)
            body_html = parsed.get("ai_body_html", "")

            if len(body_html) < 100:
                continue

            word_count = len(body_html.split())

            html += f"""
    <div class="product">
        <div class="product-header">
            <span class="product-title">Product ID: {product_id}</span>
            <span class="word-count">~{word_count} words</span>
        </div>
        <div class="description">
            {body_html}
        </div>
    </div>
"""
            shown += 1
        except Exception as e:
            html += f"""
    <div class="product">
        <div class="product-header">
            <span class="product-title">Product ID: {product_id}</span>
        </div>
        <div class="error">❌ Parse error: {escape(str(e))}</div>
    </div>
"""
            shown += 1

    html += """
    <div class="footer">
        <p>✅ If these look good, run the workflow with action: <strong>download</strong></p>
        <p>🔄 To regenerate, submit a new batch</p>
    </div>
</body>
</html>
"""

    return html


def preview_results():
    """Preview results without applying to Shopify."""
    check_env()

    batch_id = get_batch_id()
    if not batch_id:
        print("❌ No batch ID found. Provide batch_id input or run 'submit' first.")
        return

    # Check status
    print("🔍 Checking batch status...")
    response = requests.get(
        f"https://api.openai.com/v1/batches/{batch_id}",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )
    batch = response.json()

    # Show detailed status
    status = batch.get("status", "unknown")
    print(f"   Status: {status}")
    if batch.get("request_counts"):
        counts = batch.get("request_counts", {})
        print(f"   Total: {counts.get('total', 0)}, Completed: {counts.get('completed', 0)}, Failed: {counts.get('failed', 0)}")

    if status == "failed":
        print(f"❌ Batch failed!")
        if batch.get("errors"):
            print(f"   Errors: {batch.get('errors')}")
        return

    if status == "expired":
        print(f"❌ Batch expired! OpenAI batches expire after 24 hours.")
        print(f"   Please run a new 'submit' action to create a fresh batch.")
        return

    if status not in ["completed"]:
        print(f"⏳ Batch not ready yet. Current status: {status}")
        print(f"   Try again in a few minutes.")
        return

    output_file_id = batch.get("output_file_id")
    if not output_file_id:
        print("❌ No output file found (batch completed but no results).")
        error_file_id = batch.get("error_file_id")
        if error_file_id:
            print(f"   Downloading error file to see what went wrong...")
            error_response = requests.get(
                f"https://api.openai.com/v1/files/{error_file_id}/content",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
            )
            print("\n" + "="*70)
            print("ERROR DETAILS (first 5 errors):")
            print("="*70)
            for line in error_response.text.strip().split('\n')[:5]:
                try:
                    error_data = json.loads(line)
                    custom_id = error_data.get("custom_id", "unknown")
                    error = error_data.get("error", {})
                    print(f"\n Product: {custom_id}")
                    print(f"   Code: {error.get('code', 'N/A')}")
                    print(f"   Message: {error.get('message', 'N/A')}")
                except:
                    print(f"   Raw: {line[:200]}")
            print("\n" + "="*70)
        return

    # Download results
    print(f"\n📥 Downloading results...")
    response = requests.get(
        f"https://api.openai.com/v1/files/{output_file_id}/content",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )

    with open(RESULTS_FILE, "wb") as f:
        f.write(response.content)

    # Parse results
    results = []
    with open(RESULTS_FILE) as f:
        for line in f:
            results.append(json.loads(line))

    print(f"✓ Downloaded {len(results)} results")

    # Generate HTML preview
    print(f"\n📄 Generating preview report (showing {SAMPLE_COUNT} samples)...")
    html = generate_preview_html(results, SAMPLE_COUNT)

    with open(PREVIEW_FILE, "w") as f:
        f.write(html)

    print(f"✓ Saved preview to {PREVIEW_FILE}")

    # Show samples in console too
    print(f"\n{'='*70}")
    print("SAMPLE DESCRIPTIONS")
    print('='*70)

    shown = 0
    for result in results:
        if shown >= SAMPLE_COUNT:
            break

        product_id = result.get("custom_id", "Unknown")

        try:
            response_body = result.get("response", {}).get("body", {})
            choices = response_body.get("choices", [])
            if not choices:
                continue
            content = choices[0].get("message", {}).get("content", "")
            parsed = json.loads(content)
            body_html = parsed.get("ai_body_html", "")

            if len(body_html) < 100:
                continue

            # Show first 500 chars
            preview = body_html[:500].replace('\n', ' ')
            word_count = len(body_html.split())

            print(f"\n📦 Product ID: {product_id}")
            print(f"   Words: ~{word_count}")
            print(f"   Preview: {preview}...")
            print("-" * 70)

            shown += 1
        except:
            pass

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    ✅ PREVIEW COMPLETE                           ║
╠══════════════════════════════════════════════════════════════════╣
║  Total descriptions: {len(results):<44} ║
║  Samples shown: {shown:<49} ║
║  Full report: {PREVIEW_FILE:<50} ║
╠══════════════════════════════════════════════════════════════════╣
║  📥 Download the 'description-preview' artifact to view HTML     ║
║  ✅ If satisfied, run with action: download                      ║
╚══════════════════════════════════════════════════════════════════╝
""")


def download_and_apply():
    """Download batch results and update Shopify products."""
    check_env()

    batch_id = get_batch_id()
    if not batch_id:
        print("❌ No batch ID found. Provide batch_id input or run 'submit' first.")
        return

    # Check status
    print("🔍 Checking batch status...")
    response = requests.get(
        f"https://api.openai.com/v1/batches/{batch_id}",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )
    batch = response.json()

    if batch.get("status") != "completed":
        print(f"❌ Batch not ready. Status: {batch.get('status')}")
        return

    output_file_id = batch.get("output_file_id")
    if not output_file_id:
        print("❌ No output file found.")
        return

    # Download if not already
    if not Path(RESULTS_FILE).exists():
        print(f"\n📥 Downloading results...")
        response = requests.get(
            f"https://api.openai.com/v1/files/{output_file_id}/content",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
        )
        with open(RESULTS_FILE, "wb") as f:
            f.write(response.content)

    # Check for dry run
    if DRY_RUN:
        print("\n🔒 DRY RUN MODE - Not updating Shopify")
        print("   Set DRY_RUN=false to apply changes")

        # Generate preview instead
        results = []
        with open(RESULTS_FILE) as f:
            for line in f:
                results.append(json.loads(line))

        html = generate_preview_html(results, len(results))
        with open(PREVIEW_FILE, "w") as f:
            f.write(html)
        print(f"   Generated full preview: {PREVIEW_FILE}")
        return

    # Apply to Shopify
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

                if result.get("error"):
                    error_count += 1
                    continue

                response_body = result.get("response", {}).get("body", {})
                choices = response_body.get("choices", [])

                if not choices:
                    error_count += 1
                    continue

                content = choices[0].get("message", {}).get("content", "")

                try:
                    parsed = json.loads(content)
                    body_html = parsed.get("ai_body_html", "")
                except json.JSONDecodeError:
                    error_count += 1
                    continue

                if len(body_html) < 100:
                    skip_count += 1
                    continue

                # Update Shopify
                url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products/{product_id}.json"
                headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}
                payload = {"product": {"id": int(product_id), "body_html": body_html}}

                response = requests.put(url, headers=headers, json=payload)

                if response.status_code == 200:
                    success_count += 1
                else:
                    error_count += 1
                    print(f"  ✗ Failed {product_id}: {response.status_code}")

                time.sleep(0.5)  # Rate limit

            except Exception as e:
                error_count += 1

    # Generate preview for records
    results = []
    with open(RESULTS_FILE) as f:
        for line in f:
            results.append(json.loads(line))
    html = generate_preview_html(results, len(results))
    with open(PREVIEW_FILE, "w") as f:
        f.write(html)

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ COMPLETE                                 ║
╠══════════════════════════════════════════════════════════════════╣
║  ✓ Successfully updated:  {success_count:<42} ║
║  ⚠️ Skipped (too short):   {skip_count:<42} ║
║  ✗ Errors:                {error_count:<42} ║
╠══════════════════════════════════════════════════════════════════╣
║  📄 Full report saved: {PREVIEW_FILE:<41} ║
║  🛍️ Your Shopify products have been updated!                     ║
╚══════════════════════════════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser(description="Parallel Product Description Generator")
    parser.add_argument("command", choices=["submit", "status", "preview", "download"])
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
    elif args.command == "preview":
        preview_results()
    elif args.command == "download":
        download_and_apply()


if __name__ == "__main__":
    main()
