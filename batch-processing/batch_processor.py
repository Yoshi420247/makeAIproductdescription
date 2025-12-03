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
import yaml
from pathlib import Path
from datetime import datetime
from html import escape
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# === CONFIGURATION ===
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")

# Model provider selection
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "claude").lower()  # "claude" or "openai"

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

# Product limit (for testing)
PRODUCT_LIMIT = int(os.environ.get("PRODUCT_LIMIT", "0"))

BATCH_FILE = "batch_requests.jsonl"
BATCH_ID_FILE = "batch_id.txt"
RESULTS_FILE = "batch_results.jsonl"
PREVIEW_FILE = "preview_report.html"

# Parallel processing config
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "20"))  # Concurrent requests

# Model configurations
# BATCH API: Use gpt-4o (GPT-5.1 is NOT supported in OpenAI Batch API as of Dec 2025)
# REALTIME API: Use gpt-5.1 for better instruction following
# Ref: https://community.openai.com/t/batch-api-suddenly-fails-with-gpt-5-model/1343345
OPENAI_BATCH_MODEL = os.environ.get("OPENAI_BATCH_MODEL", "gpt-4o")
OPENAI_REALTIME_MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-5.1-2025-11-13")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

# Thread-safe counter for progress
progress_lock = threading.Lock()
progress_count = 0

# === BRAND GUIDE LOADING ===
BRAND_GUIDE_PATH = Path(__file__).parent / "brand-guide.yaml"
BRAND_GUIDE = None

def load_brand_guide():
    """Load the brand guide YAML file."""
    global BRAND_GUIDE
    if BRAND_GUIDE_PATH.exists():
        try:
            with open(BRAND_GUIDE_PATH, 'r') as f:
                BRAND_GUIDE = yaml.safe_load(f)
            print(f"✓ Loaded brand guide: {BRAND_GUIDE_PATH.name}")
        except Exception as e:
            print(f"⚠️ Could not load brand guide: {e}")
            BRAND_GUIDE = None
    else:
        print(f"⚠️ Brand guide not found at {BRAND_GUIDE_PATH}")
        BRAND_GUIDE = None


def build_brand_context():
    """Build a condensed brand context from the brand guide for the system prompt."""
    if not BRAND_GUIDE:
        return ""

    context_parts = []

    # Brand positioning
    site_profile = BRAND_GUIDE.get("site_profile", {})
    brand = site_profile.get("brand", {})
    if brand:
        context_parts.append(f"""
# BRAND CONTEXT: {brand.get('name', 'Oil Slick')}
Tagline: "{brand.get('tagline', '')}"
Positioning: {brand.get('positioning', '').strip()}
""")

    # Key brand traits
    traits = brand.get("key_brand_traits", [])
    if traits:
        context_parts.append("Key brand traits:\n" + "\n".join(f"- {t}" for t in traits))

    # Target audiences
    audiences = site_profile.get("audiences", {})
    primary = audiences.get("primary", [])
    if primary:
        context_parts.append("\n# TARGET AUDIENCES")
        for aud in primary:
            context_parts.append(f"- {aud.get('id', '').replace('_', ' ').title()}: {aud.get('description', '').strip()}")

    # Tone of voice
    tone = audiences.get("tone_of_voice", {})
    if tone:
        core = tone.get("core", [])
        avoid = tone.get("avoid", [])
        if core or avoid:
            context_parts.append("\n# TONE OF VOICE")
            if core:
                context_parts.append("Be: " + ", ".join(core))
            if avoid:
                context_parts.append("Avoid: " + ", ".join(avoid))

    # Internal linking with actual URLs - this is critical
    context_parts.append("""
# INTERNAL LINKING (REQUIRED - Add 3-5 links per description)

## Collection URLs to use:
- Dab Pads & Mats: https://oilslickpad.com/collections/dabbing
- Parchment Papers: https://oilslickpad.com/collections/parchment-papers
- Rosin Extraction: https://oilslickpad.com/collections/rosin-extraction
- Non-stick Paper & PTFE: https://oilslickpad.com/collections/non-stick-paper-and-ptfe
- Bulk PTFE & FEP: https://oilslickpad.com/collections/bulk-ptfe-fep
- Concentrate Jars: https://oilslickpad.com/collections/concentrate-jars
- Glass Jars & Packaging: https://oilslickpad.com/collections/glass-jars-extract-packaging
- Storage & Packaging: https://oilslickpad.com/collections/storage-packaging
- Mylar Bags: https://oilslickpad.com/collections/mylar-bags
- Joint Tubes: https://oilslickpad.com/collections/joint-tubes
- Smoke Shop Products: https://oilslickpad.com/collections/smoke-shop-products
- Silicone Pipes: https://oilslickpad.com/collections/silicone-pipes
- Silicone Bongs & Rigs: https://oilslickpad.com/collections/silicone-smoking-devices
- Nectar Collectors: https://oilslickpad.com/collections/nectar-collectors-straws
- Dab Tools: https://oilslickpad.com/collections/dab-tools-dabbers
- Carb Caps: https://oilslickpad.com/collections/carb-caps
- Accessories: https://oilslickpad.com/collections/accessories
- Clearance: https://oilslickpad.com/collections/clearance-2

## Linking Rules:
1. Add 3-5 internal links per product description (not more)
2. NEVER link to the same collection more than once per description
3. Only ONE link per paragraph - don't stack multiple links in the same block of text
4. Keep the opening paragraph (first 2-3 sentences) focused on the product itself - NO cross-links there
5. Place links in: "Best for" section, "How to use" section, FAQ answers
6. Use natural anchor text like "dab mat", "concentrate jars", "nectar collectors"

## Link Placement Examples:
- In "Best for": "Perfect for dabbers who already have a <a href="https://oilslickpad.com/collections/silicone-smoking-devices">silicone rig</a> setup."
- In "How to use": "Set it on your <a href="https://oilslickpad.com/collections/dabbing">dab mat</a> to keep your station clean."
- In FAQ: "Store unused portions in a <a href="https://oilslickpad.com/collections/concentrate-jars">concentrate jar</a> for freshness."

## Cross-linking by Product Type:
- Dab pads/mats → link to: rigs, accessories, concentrate jars
- Jars/packaging → link to: parchment papers, PTFE liners, dab pads
- Silicone pipes/rigs → link to: dab pads, accessories, carb caps
- Papers/PTFE → link to: jars, rosin extraction, storage
- Nectar collectors → link to: dab mats, concentrate jars, dab tools
- Accessories/tools → link to: pipes, rigs, dab pads
""")

    # Safety and compliance (critical)
    safety = BRAND_GUIDE.get("safety_and_compliance", {})
    constraints = safety.get("constraints_for_llm", {})
    if constraints:
        context_parts.append("\n# SAFETY & COMPLIANCE RULES (MANDATORY)")
        for category, rules in constraints.items():
            if isinstance(rules, list):
                for rule in rules:
                    context_parts.append(f"- {rule.strip()}")

    # SEO semantic clusters
    seo = BRAND_GUIDE.get("seo_semantics", {})
    clusters = seo.get("semantic_clusters", {})
    if clusters:
        context_parts.append("\n# KEYWORD CLUSTERS (use naturally)")
        for cluster_name, cluster_data in clusters.items():
            includes = cluster_data.get("includes", [])
            if includes:
                context_parts.append(f"- {cluster_name.replace('_', ' ').title()}: {', '.join(includes[:6])}")

    return "\n".join(context_parts)


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

# === SYSTEM PROMPT (LLM-SEO Optimized + Human Voice) ===
SYSTEM_PROMPT = """You are writing product descriptions for Oil Slick, a smoke shop that knows its community. Your goal: be the cleanest answer chunk on the web while sounding like a knowledgeable friend who actually uses this stuff.

# OUTPUT FORMAT
Return ONLY valid JSON: {"ai_body_html": "<p>...</p><h2>...</h2>..."}

# CRITICAL: BREAK THE TEMPLATE FEEL
AI-generated content fails when every product has identical structure. Vary your approach:
- Rename sections naturally: "How It Works" → "How to use it" or "The basics" or just skip if obvious
- "Who This Is For" → "Best for" or "Made for" or weave into the intro
- Merge small sections: "Limitations & Compatibility" can fold into specs for simple items
- FAQ length: 5-7 questions for all products — shoppers have questions, answer them thoroughly
- Bullet counts vary: 4-8 depending on what's actually worth saying
- Some products need more story in "How It Works", others just need a sentence

# STRUCTURE GUIDE (adapt, don't copy-paste)

## Opening (2-3 sentences, no heading)
This is what AI search engines cite. Include:
- What it is + category
- Who it's for (be specific)
- The main reason someone buys this
- One concrete spec that matters

Pattern: "The [Product] is a [category] built for [specific people] who [specific need]. [Key spec/differentiator] so you [actual benefit]."

## Key Benefits Section
Call it "Key Benefits" or "Why it works" or "What you get" — vary it.
Use transformation bullets: [Outcome] — [feature that makes it true]

Examples of GOOD bullets:
- "Keep your fingers back from the heat — 4.5 inch length means you're not getting toasted"
- "No more chasing tools around the coffee table — the magnetic base actually holds"
- "Fits most standard 14mm female joints — plays nice with gear you already own"

Examples of BAD bullets (too generic):
- "Premium quality construction" (says nothing)
- "Reliable performance" (meaningless)
- "Affordable price point" (let the price speak for itself)

Write 4-8 bullets based on what's actually worth saying. Not every product needs 7.

## Who It's For (optional as separate section)
If you make this a section, call it "Best for" or "Made for" or work it into the intro.
Be specific: "dabbers running 3-4 sessions a day" not "regular users"
"people with smaller hands" not "various hand sizes"

## How It Works (always include, vary depth)
Simple items: 2-3 paragraphs explaining use, tips, and context for the product category.
Complex items: Full explanation with analogies, step-by-step when helpful.
Call it "How to use it" or "The basics" or "How it works" — vary the heading.

## Specifications
Use a table for 3+ specs. Skip the section entirely for very simple items where specs are obvious.
Always dual units: "4.5 inches (114mm)"
Only include specs from the actual product data — never invent.

## What's Included (often skip)
Only include if there are multiple items or accessories. A single downstem doesn't need a "What's Included" section.

## Fit & Compatibility (merge when small)
For simple items, fold into specs or a quick note in the intro.
For complex items with real compatibility concerns, make it a section.
Be honest: "Best for daily drivers. If you're doing back-to-back dabs for hours, grab something beefier."

## FAQ (5-7 questions minimum, sound like real DMs)
Questions should sound like actual customer messages, not perfect corporate FAQs:
- "Will this fit my 14mm bong?" (real)
- "What is the compatibility of this product with 14mm joints?" (robotic)

Every product should have 5-7 FAQs covering: sizing/fit, compatibility, care/cleaning, comparisons, use cases.

# VOICE RULES

## Sound like the culture
- "dabbers" not "users" or "consumers"
- "sesh" not "session" (but don't overdo it)
- "plays nice with" not "is compatible with"
- "toasted" or "getting cooked" when talking about heat
- "daily driver" for everyday pieces
- "function" as a noun ("this rig has solid function")

## Add real-life moments (sparingly)
- "so you're not chasing sticky tools around the coffee table"
- "when you're three dabs deep and just want things to work"
- "because nobody wants to explain a broken piece to their roommate"

One or two per description, not every paragraph.

## Kill corporate stiffness
- "works conceptually with typical dab accessories" → "plays nice with most standard dab accessories you already own"
- "provides reliable performance" → just describe what it actually does
- "users will appreciate" → "you'll notice" or just state the benefit directly

## Vary sentence rhythm
Mix it up:
- Short punchy. Like this.
- Then longer sentences that explain the technical details when you need to get into the specifics of how something actually works.
- Questions work too. Ever tried spinning a cap without grip rings?

# CATEGORY KNOWLEDGE

**Downstems**: Effective length (shoulder to tip matters more than total length), joint size (10/14/18mm), angle (45°/90°), diffusion style (slits, holes, fire-cut)
**Bongs/Water Pipes**: Perc type and what it actually does for the hit, joint specs, ice pinch, base stability, cleaning difficulty
**Grinders**: Piece count, kief catch, tooth design (diamond vs shark), material
**Dab Rigs**: Banger compatibility, joint size/gender, recycler function, recommended nail material
**Carb Caps**: Directional vs bubble vs spinner, what rigs they work with, airflow style
**Dab Tools**: Material (titanium vs glass vs ceramic), length, tip style, cleaning
**Hand Pipes**: Bowl size, carb placement, portability, heat dissipation
**Vaporizers**: Heating method (conduction/convection), temp control, battery life, chamber size
**Silicone Products**: Food-grade silicone callouts, heat resistance, cleaning

# INTERNAL LINKING (REQUIRED)

Every product description MUST include 3-5 internal links to related collections. This is critical for SEO.

STRICT RULES:
- NEVER link to the same collection more than once per description
- Only ONE link per paragraph - don't stack multiple links together
- Keep the opening paragraph (first 2-3 sentences) about the product only - NO links there
- The opening should nail the "what / who / why" without cross-links

How to add links:
- Use HTML anchor tags: <a href="URL">anchor text</a>
- Place links naturally within sentences, not as a standalone list
- Use descriptive anchor text that matches search terms (e.g., "dab mat", "concentrate jars")

Where to place links:
- In the "Best for" or "Who it's for" section
- In the "How to use" section
- In FAQ answers when relevant
- NOT in the opening paragraph

Example link placements:
- "Set it on your <a href="https://oilslickpad.com/collections/dabbing">dab mat</a> to keep things clean"
- "Store your extracts in a <a href="https://oilslickpad.com/collections/concentrate-jars">concentrate jar</a>"
- "Pairs well with any <a href="https://oilslickpad.com/collections/silicone-smoking-devices">silicone rig</a>"

The Brand Context section below contains the full list of collection URLs to use.

# CLEANING ADVICE

Keep cleaning instructions short and generic:
- "Let it cool, then wipe off residue. For deeper cleans, soak in isopropyl alcohol, rinse and dry before reuse."
- Don't add extra steps or make it sound like a detailed how-to guide
- Keep it safe and simple

# RULES
- 1,200-1,600 words minimum — comprehensive descriptions perform better for SEO and AI search
- Never invent specs — only use what's in the product data
- HTML tags: h2, h3, p, ul, ol, li, strong, em, table, tr, th, td, a (for internal links)
- Dual units: "4.5 inches (114mm)"
- Include thorough FAQ sections (5-7 questions), detailed feature explanations, and proper category context
- MUST include 3-5 internal links, each to a DIFFERENT collection, one per paragraph max
- Keep opening paragraph link-free — focus on product only
"""


def get_full_system_prompt():
    """Get the full system prompt including brand context."""
    brand_context = build_brand_context()
    if brand_context:
        return SYSTEM_PROMPT + "\n\n" + brand_context
    return SYSTEM_PROMPT


def check_env():
    """Verify all required environment variables are set."""
    missing = []

    # Check API key based on provider
    if MODEL_PROVIDER == "claude":
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    else:
        if not OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")

    if not SHOPIFY_STORE:
        missing.append("SHOPIFY_STORE")
    if not SHOPIFY_ACCESS_TOKEN:
        missing.append("SHOPIFY_ACCESS_TOKEN")

    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        exit(1)

    # Load brand guide
    load_brand_guide()

    print(f"🤖 Using model provider: {MODEL_PROVIDER.upper()}")
    if MODEL_PROVIDER == "claude":
        print(f"   Model: {CLAUDE_MODEL}")
    else:
        print(f"   Batch Model: {OPENAI_BATCH_MODEL} (GPT-5.1 not supported in Batch API)")
        print(f"   Realtime Model: {OPENAI_REALTIME_MODEL}")


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

    # Apply product limit (for testing)
    if PRODUCT_LIMIT > 0:
        print(f"  Limiting to first {PRODUCT_LIMIT} products (test mode)")
        filtered = filtered[:PRODUCT_LIMIT]

    return filtered


def build_product_prompt(product):
    """Build the user prompt for a product (shared by all providers)."""
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

    return f"""Write a product description for:

Title: {title}
Vendor: {vendor if vendor else 'Oil Slick'}
Type: {product_type}
Tags: {tags}
Options: {options_str}
Variants: {variants_str}
Current Description: {body_html if body_html else '(none)'}

Return JSON only: {{"ai_body_html": "..."}}"""


def create_batch_request_openai(product):
    """Create an OpenAI batch request for a product."""
    user_content = build_product_prompt(product)
    system_prompt = get_full_system_prompt()

    return {
        "custom_id": str(product["id"]),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": OPENAI_BATCH_MODEL,
            "response_format": {"type": "json_object"},
            "max_tokens": 16000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
        }
    }


def create_batch_request_claude(product):
    """Create a Claude batch request for a product."""
    user_content = build_product_prompt(product)
    system_prompt = get_full_system_prompt()

    return {
        "custom_id": str(product["id"]),
        "params": {
            "model": CLAUDE_MODEL,
            "max_tokens": 16000,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_content}
            ]
        }
    }


def create_batch_request(product):
    """Create a batch request for a product using the selected provider."""
    if MODEL_PROVIDER == "claude":
        return create_batch_request_claude(product)
    else:
        return create_batch_request_openai(product)


def submit_batch_openai(products):
    """Submit a batch job to OpenAI."""
    print(f"\n📝 STEP 2: Creating batch file with {len(products)} requests...")
    with open(BATCH_FILE, "w") as f:
        for i, product in enumerate(products):
            if i % 50 == 0:
                print(f"  Processing product {i+1}/{len(products)}...")
            request = create_batch_request_openai(product)
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

    return response.json()["id"]


def submit_batch_claude(products):
    """Submit a batch job to Claude (Anthropic Message Batches API)."""
    print(f"\n📝 STEP 2: Creating batch requests for {len(products)} products...")

    requests_list = []
    for i, product in enumerate(products):
        if i % 50 == 0:
            print(f"  Processing product {i+1}/{len(products)}...")
        requests_list.append(create_batch_request_claude(product))

    print(f"✓ Created {len(requests_list)} requests")

    print("\n🚀 STEP 3: Submitting batch to Claude...")
    response = requests.post(
        "https://api.anthropic.com/v1/messages/batches",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        },
        json={"requests": requests_list}
    )

    if response.status_code not in [200, 201]:
        print(f"❌ Failed to create batch: {response.status_code}")
        print(response.text)
        exit(1)

    return response.json()["id"]


def submit_batch():
    """Create and submit a batch job."""
    check_env()
    print("\n📦 STEP 1: Fetching products from Shopify...")
    products = get_shopify_products()

    if not products:
        print("❌ No products found matching your filters!")
        return

    provider_name = "Claude" if MODEL_PROVIDER == "claude" else "OpenAI"

    if MODEL_PROVIDER == "claude":
        batch_id = submit_batch_claude(products)
        estimated_cost = len(products) * 0.015  # Claude batch is ~50% cheaper
    else:
        batch_id = submit_batch_openai(products)
        estimated_cost = len(products) * 0.025

    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch_id)

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ BATCH SUBMITTED                          ║
╠══════════════════════════════════════════════════════════════════╣
║  Provider:      {provider_name:<48} ║
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


def is_claude_batch(batch_id):
    """Check if a batch ID is from Claude (starts with msgbatch_)."""
    return batch_id.startswith("msgbatch_")


def check_status_openai(batch_id):
    """Check OpenAI batch status."""
    response = requests.get(
        f"https://api.openai.com/v1/batches/{batch_id}",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
    )

    if response.status_code != 200:
        print(f"❌ Failed to get batch status: {response.status_code}")
        return None

    batch = response.json()
    counts = batch.get("request_counts", {})
    return {
        "status": batch.get("status"),
        "total": counts.get("total", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "output_file_id": batch.get("output_file_id"),
        "error_file_id": batch.get("error_file_id"),
        "raw": batch
    }


def check_status_claude(batch_id):
    """Check Claude batch status."""
    response = requests.get(
        f"https://api.anthropic.com/v1/messages/batches/{batch_id}",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )

    if response.status_code != 200:
        print(f"❌ Failed to get batch status: {response.status_code}")
        return None

    batch = response.json()
    counts = batch.get("request_counts", {})

    # Claude uses different status names
    status_map = {
        "in_progress": "in_progress",
        "ended": "completed",
        "canceling": "canceling",
        "canceled": "canceled"
    }

    return {
        "status": status_map.get(batch.get("processing_status"), batch.get("processing_status")),
        "total": counts.get("processing", 0) + counts.get("succeeded", 0) + counts.get("errored", 0) + counts.get("canceled", 0) + counts.get("expired", 0),
        "completed": counts.get("succeeded", 0),
        "failed": counts.get("errored", 0) + counts.get("canceled", 0) + counts.get("expired", 0),
        "results_url": batch.get("results_url"),
        "raw": batch
    }


def check_status():
    """Check the status of the current batch job."""
    check_env()

    batch_id = get_batch_id()
    if not batch_id:
        print("❌ No batch ID found. Provide batch_id input or run 'submit' first.")
        return None

    # Detect provider from batch ID format
    is_claude = is_claude_batch(batch_id)
    provider_name = "Claude" if is_claude else "OpenAI"

    print(f"🔍 Checking {provider_name} batch: {batch_id}")

    if is_claude:
        batch_info = check_status_claude(batch_id)
    else:
        batch_info = check_status_openai(batch_id)

    if not batch_info:
        return None

    total = batch_info["total"]
    completed = batch_info["completed"]
    failed = batch_info["failed"]
    status = batch_info["status"]
    progress = (completed / total * 100) if total > 0 else 0

    status_emoji = {"validating": "🔄", "in_progress": "⏳", "finalizing": "📦", "completed": "✅", "ended": "✅", "failed": "❌", "expired": "⚠️", "canceled": "⛔"}.get(status, "❓")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                       BATCH STATUS                               ║
╠══════════════════════════════════════════════════════════════════╣
║  Provider:    {provider_name:<50} ║
║  Batch ID:    {batch_id:<50} ║
║  Status:      {status_emoji} {status:<48} ║
║  Progress:    {completed}/{total} ({progress:.1f}% complete){' ':<27} ║
║  Failed:      {failed:<50} ║""")

    if status in ["completed", "ended"]:
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ✅ READY!                                                        ║
║  → Run 'preview' to see samples before applying                  ║
║  → Run 'download' to apply all to Shopify                        ║
╚══════════════════════════════════════════════════════════════════╝
""")
    elif status == "in_progress":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ⏳ Still processing... Check again in a few minutes.            ║
╚══════════════════════════════════════════════════════════════════╝
""")
    else:
        print(f"""╚══════════════════════════════════════════════════════════════════╝
""")

    return batch_info


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


def process_single_product_openai(product, total_count):
    """Process a single product using OpenAI GPT-5.1 API."""
    global progress_count

    product_id = str(product["id"])
    title = product.get("title", "Unknown")

    # Build the prompt
    body_html = product.get("body_html", "") or ""
    tags = product.get("tags", "") or ""
    product_type = product.get("product_type", "") or ""
    vendor = product.get("vendor", "") or ""

    variants = product.get("variants", [])
    variant_info = ""
    if variants:
        prices = [v.get("price", "0") for v in variants[:5]]
        variant_info = f"Price range: ${min(prices)} - ${max(prices)}" if len(set(prices)) > 1 else f"Price: ${prices[0]}"

    user_content = f"""Product: {title}
Type: {product_type}
Vendor: {vendor}
Tags: {tags}
{variant_info}
Current Description: {body_html if body_html else '(none)'}

Return JSON only: {{"ai_body_html": "..."}}"""

    system_prompt = get_full_system_prompt()

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENAI_REALTIME_MODEL,
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 16000,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ]
            },
            timeout=120
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            body_html = parsed.get("ai_body_html", "")

            with progress_lock:
                progress_count += 1
                if progress_count % 10 == 0 or progress_count == total_count:
                    print(f"  ✓ Progress: {progress_count}/{total_count} ({100*progress_count/total_count:.1f}%)")

            return {
                "product_id": product_id,
                "title": title,
                "body_html": body_html,
                "success": True,
                "error": None
            }
        elif response.status_code == 429:
            time.sleep(5)
            return process_single_product_openai(product, total_count)
        else:
            error_msg = response.json().get("error", {}).get("message", f"Status {response.status_code}")
            return {
                "product_id": product_id,
                "title": title,
                "body_html": "",
                "success": False,
                "error": error_msg
            }
    except Exception as e:
        return {
            "product_id": product_id,
            "title": title,
            "body_html": "",
            "success": False,
            "error": str(e)
        }


def process_single_product_claude(product, total_count):
    """Process a single product using Claude Sonnet 4.5 API."""
    global progress_count

    product_id = str(product["id"])
    title = product.get("title", "Unknown")

    # Build the prompt
    body_html = product.get("body_html", "") or ""
    tags = product.get("tags", "") or ""
    product_type = product.get("product_type", "") or ""
    vendor = product.get("vendor", "") or ""

    variants = product.get("variants", [])
    variant_info = ""
    if variants:
        prices = [v.get("price", "0") for v in variants[:5]]
        variant_info = f"Price range: ${min(prices)} - ${max(prices)}" if len(set(prices)) > 1 else f"Price: ${prices[0]}"

    user_content = f"""Product: {title}
Type: {product_type}
Vendor: {vendor}
Tags: {tags}
{variant_info}
Current Description: {body_html if body_html else '(none)'}

Return JSON only: {{"ai_body_html": "..."}}"""

    system_prompt = get_full_system_prompt()

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 16000,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_content}
                ]
            },
            timeout=120
        )

        if response.status_code == 200:
            try:
                result = response.json()
            except json.JSONDecodeError:
                return {
                    "product_id": product_id,
                    "title": title,
                    "body_html": "",
                    "success": False,
                    "error": f"Invalid JSON response from API: {response.text[:200]}"
                }
            # Claude returns content as array of blocks
            content_blocks = result.get("content", [])
            content = ""
            for block in content_blocks:
                if block.get("type") == "text":
                    content = block.get("text", "")
                    break

            if not content:
                return {
                    "product_id": product_id,
                    "title": title,
                    "body_html": "",
                    "success": False,
                    "error": f"Empty response from Claude. Stop reason: {result.get('stop_reason')}"
                }

            # Claude may wrap JSON in markdown code blocks - extract it
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            # Try to find JSON object if there's extra text
            if not content.startswith("{"):
                start_idx = content.find("{")
                end_idx = content.rfind("}") + 1
                if start_idx != -1 and end_idx > start_idx:
                    content = content[start_idx:end_idx]

            # Parse the JSON response
            parsed = json.loads(content)
            body_html = parsed.get("ai_body_html", "")

            with progress_lock:
                progress_count += 1
                if progress_count % 10 == 0 or progress_count == total_count:
                    print(f"  ✓ Progress: {progress_count}/{total_count} ({100*progress_count/total_count:.1f}%)")

            return {
                "product_id": product_id,
                "title": title,
                "body_html": body_html,
                "success": True,
                "error": None
            }
        elif response.status_code == 429:
            time.sleep(5)
            return process_single_product_claude(product, total_count)
        else:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", f"Status {response.status_code}")
            except:
                error_msg = f"Status {response.status_code}: {response.text[:200]}"
            return {
                "product_id": product_id,
                "title": title,
                "body_html": "",
                "success": False,
                "error": error_msg
            }
    except json.JSONDecodeError as e:
        # Content wasn't empty but wasn't valid JSON
        return {
            "product_id": product_id,
            "title": title,
            "body_html": "",
            "success": False,
            "error": f"JSON parse error. Response preview: {content[:300] if 'content' in dir() else 'N/A'}"
        }
    except Exception as e:
        return {
            "product_id": product_id,
            "title": title,
            "body_html": "",
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}"
        }


def process_single_product(product, total_count):
    """Process a single product using the selected AI provider."""
    if MODEL_PROVIDER == "claude":
        return process_single_product_claude(product, total_count)
    else:
        return process_single_product_openai(product, total_count)


def realtime_process():
    """Process all products using parallel real-time API calls."""
    global progress_count
    progress_count = 0

    check_env()

    print(f"\n📦 STEP 1: Fetching products from Shopify...")
    products = get_shopify_products()

    if not products:
        print("❌ No products to process.")
        return

    total = len(products)
    model_name = CLAUDE_MODEL if MODEL_PROVIDER == "claude" else OPENAI_REALTIME_MODEL
    provider_name = "Claude" if MODEL_PROVIDER == "claude" else "OpenAI"

    print(f"\n🚀 STEP 2: Processing {total} products with {provider_name} (parallel)...")
    print(f"   Model: {model_name}")
    print(f"   Workers: {MAX_WORKERS} concurrent requests")
    print(f"   Estimated time: {total * 3 / MAX_WORKERS / 60:.1f} minutes\n")

    results = []
    success_count = 0
    error_count = 0

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_product, p, total): p for p in products}

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result["success"]:
                success_count += 1
            else:
                error_count += 1
                if error_count <= 5:
                    print(f"  ✗ Error on {result['product_id']}: {result['error'][:100]}")

    elapsed = time.time() - start_time

    # Save results to JSONL (same format as batch for compatibility)
    with open(RESULTS_FILE, "w") as f:
        for r in results:
            if r["success"]:
                line = {
                    "custom_id": r["product_id"],
                    "response": {
                        "body": {
                            "choices": [{
                                "message": {
                                    "content": json.dumps({"ai_body_html": r["body_html"]})
                                }
                            }]
                        }
                    }
                }
                f.write(json.dumps(line) + "\n")

    # Generate preview HTML
    print(f"\n📄 STEP 3: Generating preview report...")
    preview_results_data = []
    with open(RESULTS_FILE) as f:
        for line in f:
            preview_results_data.append(json.loads(line))

    html = generate_preview_html(preview_results_data, min(SAMPLE_COUNT, len(preview_results_data)))
    with open(PREVIEW_FILE, "w") as f:
        f.write(html)

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║              ✅ REALTIME PROCESSING COMPLETE                     ║
╠══════════════════════════════════════════════════════════════════╣
║  Provider:    {provider_name:<50} ║
║  Model:       {model_name:<50} ║
║  Products:    {total:<50} ║
║  Successful:  {success_count:<50} ║
║  Failed:      {error_count:<50} ║
║  Time:        {elapsed/60:.1f} minutes{' ':<43} ║
╠══════════════════════════════════════════════════════════════════╣
║  📄 Preview report: {PREVIEW_FILE:<44} ║
║  📥 Download 'description-preview' artifact to review            ║
║  ✅ Run 'apply' action to update Shopify                         ║
╚══════════════════════════════════════════════════════════════════╝
""")


def apply_results():
    """Apply saved results to Shopify (for realtime mode)."""
    check_env()

    if not Path(RESULTS_FILE).exists():
        print("❌ No results file found. Run 'realtime' first.")
        return

    results = []
    with open(RESULTS_FILE) as f:
        for line in f:
            results.append(json.loads(line))

    if not results:
        print("❌ No results to apply.")
        return

    total = len(results)
    print(f"\n🔄 Applying {total} descriptions to Shopify...")

    if DRY_RUN:
        print("🔒 DRY RUN MODE - Not updating Shopify")
        return

    success_count = 0
    error_count = 0
    skip_count = 0

    for i, result in enumerate(results):
        try:
            product_id = result.get("custom_id")
            content = result.get("response", {}).get("body", {}).get("choices", [{}])[0].get("message", {}).get("content", "{}")
            parsed = json.loads(content)
            body_html = parsed.get("ai_body_html", "")

            if (i + 1) % 50 == 0:
                print(f"  Processing {i+1}/{total}...")

            if len(body_html) < 100:
                skip_count += 1
                continue

            url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products/{product_id}.json"
            headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}
            payload = {"product": {"id": int(product_id), "body_html": body_html}}

            response = requests.put(url, headers=headers, json=payload)

            if response.status_code == 200:
                success_count += 1
            else:
                error_count += 1

            time.sleep(0.5)  # Shopify rate limit

        except Exception as e:
            error_count += 1

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ SHOPIFY UPDATED                          ║
╠══════════════════════════════════════════════════════════════════╣
║  ✓ Successfully updated:  {success_count:<42} ║
║  ⚠️ Skipped (too short):   {skip_count:<42} ║
║  ✗ Errors:                {error_count:<42} ║
╚══════════════════════════════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser(description="Parallel Product Description Generator")
    parser.add_argument("command", choices=["submit", "status", "preview", "download", "realtime", "apply"])
    args = parser.parse_args()

    # Determine provider info for headers
    provider_name = "Claude Sonnet 4.5" if MODEL_PROVIDER == "claude" else "GPT-5.1"

    if args.command == "realtime":
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           Using {provider_name} Real-Time API (parallel){' '*(21-len(provider_name))}║
╚══════════════════════════════════════════════════════════════════╝
        """)
        realtime_process()
    elif args.command == "apply":
        print("""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           Applying Results to Shopify                            ║
╚══════════════════════════════════════════════════════════════════╝
        """)
        apply_results()
    else:
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
