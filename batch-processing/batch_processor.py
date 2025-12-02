#!/usr/bin/env python3
"""
Parallel Product Description Generator using OpenAI Batch API

Process hundreds or thousands of Shopify products simultaneously.
50% cheaper than synchronous API calls, no rate limits.

Usage:
    python batch_processor.py submit    # Create batch from Shopify products
    python batch_processor.py status    # Check batch status
    python batch_processor.py download  # Download and apply results

Requirements:
    pip install openai shopify python-dotenv
"""

import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# === CONFIGURATION ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")  # e.g., "oil-slick-pad.myshopify.com"
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

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

# RULES
- Length: 1,200-1,600 words. Quality over padding.
- No fabricated specs. Only use provided data.
- HTML tags: h2, h3, p, ul, ol, li, strong, em, a, table
- Dual units: "4.5 inches (114mm)"
"""


def get_shopify_products():
    """Fetch all active products from Shopify."""
    import requests

    url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}
    params = {"status": "active", "limit": 250}

    all_products = []
    while url:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        all_products.extend(data.get("products", []))

        # Handle pagination
        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    url = link.split(";")[0].strip("<> ")
        params = {}  # Clear params for paginated requests

    print(f"✓ Fetched {len(all_products)} products from Shopify")
    return all_products


def create_batch_request(product):
    """Create a single batch request for a product."""
    user_content = f"""Write a product description for:

Title: {product.get('title', '')}
Vendor: Oil Slick
Type: {product.get('product_type', '')}
Tags: {', '.join(product.get('tags', '').split(','))}
Options: {json.dumps(product.get('options', []))}
Variants: {json.dumps([{'title': v.get('title'), 'price': v.get('price')} for v in product.get('variants', [])])}
Current Description: {product.get('body_html', '')[:500]}

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
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Get products
    products = get_shopify_products()

    if not products:
        print("No products found!")
        return

    # Create JSONL file
    print(f"Creating batch file with {len(products)} requests...")
    with open(BATCH_FILE, "w") as f:
        for product in products:
            request = create_batch_request(product)
            f.write(json.dumps(request) + "\n")

    # Upload file
    print("Uploading batch file to OpenAI...")
    with open(BATCH_FILE, "rb") as f:
        batch_file = client.files.create(file=f, purpose="batch")
    print(f"✓ File uploaded: {batch_file.id}")

    # Create batch job
    print("Creating batch job...")
    batch_job = client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h"
    )

    # Save batch ID
    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch_job.id)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    BATCH SUBMITTED                           ║
╠══════════════════════════════════════════════════════════════╣
║  Batch ID: {batch_job.id:<47} ║
║  Products: {len(products):<47} ║
║  Status:   {batch_job.status:<47} ║
║  Window:   24 hours (usually much faster)                    ║
╠══════════════════════════════════════════════════════════════╣
║  Next: Run 'python batch_processor.py status' to check       ║
╚══════════════════════════════════════════════════════════════╝
""")


def check_status():
    """Check the status of the current batch job."""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    if not Path(BATCH_ID_FILE).exists():
        print("No batch ID found. Run 'submit' first.")
        return

    with open(BATCH_ID_FILE) as f:
        batch_id = f.read().strip()

    batch = client.batches.retrieve(batch_id)

    # Calculate progress
    total = batch.request_counts.total
    completed = batch.request_counts.completed
    failed = batch.request_counts.failed
    progress = (completed / total * 100) if total > 0 else 0

    status_emoji = {
        "validating": "🔄",
        "in_progress": "⏳",
        "completed": "✅",
        "failed": "❌",
        "expired": "⚠️",
        "cancelled": "🚫"
    }.get(batch.status, "❓")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    BATCH STATUS                              ║
╠══════════════════════════════════════════════════════════════╣
║  Batch ID:  {batch_id:<46} ║
║  Status:    {status_emoji} {batch.status:<44} ║
║  Progress:  {completed}/{total} ({progress:.1f}%){'':.<34} ║
║  Failed:    {failed:<47} ║
╠══════════════════════════════════════════════════════════════╣""")

    if batch.status == "completed":
        print(f"""║  Output:    {batch.output_file_id:<46} ║
╠══════════════════════════════════════════════════════════════╣
║  ✓ READY! Run 'python batch_processor.py download'           ║
╚══════════════════════════════════════════════════════════════╝
""")
    elif batch.status == "in_progress":
        print(f"""║  Check again in a few minutes...                             ║
╚══════════════════════════════════════════════════════════════╝
""")
    else:
        print(f"""╚══════════════════════════════════════════════════════════════╝
""")


def download_and_apply():
    """Download batch results and update Shopify products."""
    import requests
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    if not Path(BATCH_ID_FILE).exists():
        print("No batch ID found. Run 'submit' first.")
        return

    with open(BATCH_ID_FILE) as f:
        batch_id = f.read().strip()

    batch = client.batches.retrieve(batch_id)

    if batch.status != "completed":
        print(f"Batch not ready yet. Status: {batch.status}")
        return

    # Download results
    print("Downloading results...")
    result_content = client.files.content(batch.output_file_id)

    with open(RESULTS_FILE, "wb") as f:
        f.write(result_content.content)

    # Parse and apply results
    print("Applying results to Shopify...")

    success_count = 0
    error_count = 0

    with open(RESULTS_FILE) as f:
        for line in f:
            try:
                result = json.loads(line)
                product_id = result["custom_id"]

                # Extract AI-generated HTML
                content = result["response"]["body"]["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                body_html = parsed.get("ai_body_html", "")

                if len(body_html) < 100:
                    print(f"  ⚠️ Skipping {product_id}: Empty or too short")
                    continue

                # Update Shopify
                url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products/{product_id}.json"
                headers = {
                    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
                    "Content-Type": "application/json"
                }
                payload = {"product": {"id": product_id, "body_html": body_html}}

                response = requests.put(url, headers=headers, json=payload)

                if response.status_code == 200:
                    success_count += 1
                    print(f"  ✓ Updated product {product_id}")
                else:
                    error_count += 1
                    print(f"  ✗ Failed to update {product_id}: {response.status_code}")

                # Rate limiting for Shopify API
                time.sleep(0.5)

            except Exception as e:
                error_count += 1
                print(f"  ✗ Error processing result: {e}")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    COMPLETE                                  ║
╠══════════════════════════════════════════════════════════════╣
║  ✓ Updated: {success_count:<47} ║
║  ✗ Errors:  {error_count:<47} ║
╚══════════════════════════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser(
        description="Parallel Product Description Generator using OpenAI Batch API"
    )
    parser.add_argument(
        "command",
        choices=["submit", "status", "download"],
        help="Command to run"
    )
    args = parser.parse_args()

    if args.command == "submit":
        submit_batch()
    elif args.command == "status":
        check_status()
    elif args.command == "download":
        download_and_apply()


if __name__ == "__main__":
    main()
