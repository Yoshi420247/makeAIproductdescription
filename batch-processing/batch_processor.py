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
import re
import time
import argparse
import requests
import yaml
from pathlib import Path
from datetime import datetime, timezone
from html import escape
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import uuid

import supabase_store as store

# === CONFIGURATION ===
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")

# Model provider selection: "claude-sonnet", "claude-opus", "claude", or "openai"
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "claude-sonnet").lower()

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

# Auto-apply: Push to Shopify immediately as descriptions are generated
AUTO_APPLY = os.environ.get("AUTO_APPLY", "false").lower() == "true"

# Batch ID (can be passed directly via env var)
BATCH_ID_INPUT = os.environ.get("BATCH_ID", "").strip()

# Product limit (for testing)
PRODUCT_LIMIT = int(os.environ.get("PRODUCT_LIMIT", "0"))

# Content source: "product_data" (raw Shopify data) or "existing_pdp" (rewrite existing description)
CONTENT_SOURCE = os.environ.get("CONTENT_SOURCE", "product_data").lower()

# Skip already-optimized PDPs (auto-detect and skip products with quality descriptions)
SKIP_OPTIMIZED = os.environ.get("SKIP_OPTIMIZED", "false").lower() == "true"

# Supabase run ID (for apply/rollback targeting a specific run)
TARGET_RUN_ID = os.environ.get("RUN_ID", "").strip()

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

# Claude model mapping
CLAUDE_MODELS = {
    "claude-sonnet": "claude-sonnet-4-5-20250929",
    "claude-opus": "claude-opus-4-5-20251101",
    "claude": "claude-sonnet-4-5-20250929",  # legacy default
}
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or CLAUDE_MODELS.get(MODEL_PROVIDER, "claude-sonnet-4-5-20250929")
IS_CLAUDE = MODEL_PROVIDER.startswith("claude")

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

    # Internal linking with actual URLs - expanded with secondary collections
    context_parts.append("""
# COMPLETE COLLECTION URL REFERENCE

## Primary Oil Slick Collections:
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

## Secondary Collections (use when relevant to product context):
DABBING CATEGORY:
- Dab Rigs: https://oilslickpad.com/collections/dab-rigs
- Quartz Bangers: https://oilslickpad.com/collections/quartz-bangers
- Torches: https://oilslickpad.com/collections/torches

SMOKING CATEGORY:
- Bubblers: https://oilslickpad.com/collections/bubblers
- Grinders: https://oilslickpad.com/collections/grinders
- Hand Pipes: https://oilslickpad.com/collections/hand-pipes

ROLLING CATEGORY:
- Rolling Papers: https://oilslickpad.com/collections/rolling-papers
- Pre-Roll Cones: https://oilslickpad.com/collections/rolling-papers-cones

## Cross-linking by Product Type:
- Dab pads/mats → link to: rigs, accessories, concentrate jars, carb caps
- Jars/packaging → link to: parchment papers, PTFE liners, dab pads, mylar bags
- Silicone pipes/rigs → link to: dab pads, accessories, carb caps, grinders
- Papers/PTFE → link to: jars, rosin extraction, storage
- Nectar collectors → link to: dab mats, concentrate jars, dab tools, torches
- Accessories/tools → link to: pipes, rigs, dab pads, bangers
- Bangers/caps → link to: dab rigs, torches, dab tools, dab mats

## Link Placement Examples:
- In benefits: "Perfect for dabbers who already have a <a href="https://oilslickpad.com/collections/silicone-smoking-devices">silicone rig</a> setup."
- In how-to: "Set it on your <a href="https://oilslickpad.com/collections/dabbing">dab mat</a> to keep your station clean."
- In FAQ: "Store unused portions in a <a href="https://oilslickpad.com/collections/concentrate-jars">concentrate jar</a> for freshness."
- External link example: For more on concentrate storage, check <a href="https://www.leafly.com/learn/cannabis-glossary/concentrates" rel="noopener noreferrer" target="_blank">Leafly's guide to concentrates</a>.
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

# === SYSTEM PROMPT (LLM-SEO Optimized + Human Voice + Humanizer) ===
SYSTEM_PROMPT = """You are writing product descriptions for Oil Slick, a smoke shop that knows its community. Your goal: be the cleanest answer chunk on the web while sounding like a knowledgeable friend who actually uses this stuff.

# OUTPUT FORMAT
Return ONLY valid JSON: {"ai_body_html": "<p>...</p><h2>...</h2>..."}

# SKU & REFERENCE NUMBERS (CRITICAL)
Internal SKUs, part numbers, model codes, and reference numbers belong ONLY in structured data (specs table).
- NEVER include SKUs in the opening paragraph, headings, or flowing body copy
- NEVER start sentences with model numbers like "The XJ-4500 is..."
- In specs table: "Model: XJ-4500" or "SKU: OS-PAD-001" is fine
- In body copy: describe the product by what it IS, not its code
- If the product title contains a model number, use it once in the opening, then refer to the product naturally

# SPELLING & GRAMMAR CORRECTION
Automatically fix obvious spelling mistakes from the source data:
- Common typos: "silicone" not "silicon" (for the material), "banger" not "bangar", "quartz" not "quarts"
- Product terms: "parchment" not "parchement", "concentrates" not "consentrates"
- Cannabis terms: "terpenes" not "turpenes", "dabbing" not "dabbbing"
- Do NOT flag or mention corrections — just fix them silently in your output

# TITLE OPTIMIZATION FOR SEO
When the product title needs improvement, optimize it for the niche:
- Lead with the product type + key differentiator: "Silicone Dab Pad" not "Pad - Silicone"
- Include size/capacity when relevant: "9ml Child-Resistant Concentrate Jar"
- Use search-friendly terms: "Nectar Collector" not "Honey Straw Device"
- Keep under 60 characters when possible for search display
- Format: [Brand] [Product Type] [Key Feature] [Size/Variant]
- Example: "Oil Slick Silicone Dab Mat - Large 12x12 inch"

# COPYRIGHT & TRADEMARK COMPLIANCE (MANDATORY)
Replace copyrighted character names with creative, legally-safe alternatives:

STAR WARS:
- "Grogu" / "Baby Yoda" → "Space Goblin", "Galaxy Gremlin", "Little Green Guy", "Cosmic Critter"
- "Yoda" → "Wise Green Elder", "Swamp Sage", "Green Mystic"
- "Darth Vader" → "Dark Lord", "Space Villain", "Galactic Enforcer"
- "Stormtrooper" → "Space Trooper", "Galactic Soldier"
- "Mandalorian" → "Armored Bounty Hunter", "Helmet Warrior"

DISNEY/PIXAR:
- "Mickey Mouse" → "Classic Mouse", "Cartoon Mouse"
- "Stitch" → "Blue Alien Creature", "Alien Critter"

ANIME/MANGA:
- "Pikachu" / Pokemon → "Electric Critter", "Yellow Creature", "Pocket Monster style"
- "Naruto" → "Ninja Style", "Shinobi Design"
- "Dragon Ball" characters → "Anime Warrior", "Power Fighter"

GENERAL RULES:
- NEVER use trademarked names in the description body
- Use descriptive alternatives that evoke the design without infringing
- "Inspired by" or "style" language is safer than direct references
- When in doubt, describe the visual (color, shape, expression) instead of the character
- Example: "Grogu Pipe" → "Little Green Guy Silicone Pipe" with description mentioning "big-eared green creature design"

# HUMANIZER: REMOVE AI WRITING PATTERNS
Your writing must pass as human-written. Avoid these AI tells:

## Content Patterns to AVOID:
- Significance inflation: "stands as a testament", "pivotal moment", "marks a shift", "key turning point", "indelible mark"
- Promotional fluff: "boasts", "vibrant", "profound", "showcasing", "exemplifies", "nestled", "breathtaking", "stunning", "groundbreaking"
- Superficial -ing phrases: "highlighting the importance", "ensuring quality", "reflecting the commitment", "symbolizing excellence"
- Vague attributions: "Experts say", "Industry reports show", "Observers note" — be specific or don't cite
- Challenges/prospects formula: Don't end with "Despite challenges, the future looks bright"

## Language Patterns to AVOID:
- AI vocabulary overload: Additionally, crucial, delve, emphasizing, enduring, enhance, fostering, garner, highlight (verb), interplay, intricate, landscape (abstract), pivotal, showcase, tapestry (abstract), testament, underscore, valuable
- Copula avoidance: Don't write "serves as" or "stands as" when "is" works fine
- Negative parallelisms: "It's not just X, it's Y" or "Not only...but also" — overused
- Rule of three: Don't force every list into exactly three items
- False ranges: "from X to Y, from A to B" when X and Y aren't on a real scale
- Synonym cycling: Don't swap "bong" → "water pipe" → "smoking device" → "piece" in adjacent sentences just to avoid repetition

## Style Patterns to AVOID:
- Em dash overuse: Use commas and periods instead of cramming — dashes — everywhere
- Excessive boldface: Don't bold every keyword
- Inline-header lists: Don't do "**Feature:** description" for every bullet
- Title Case In Every Heading: Use sentence case instead
- Emojis: Never use emojis in product descriptions

## Communication Artifacts to AVOID:
- "I hope this helps", "Let me know if you need more", "Here is a..." — these are chatbot tells
- Sycophantic phrases: "Great question!", "You're absolutely right!", "Excellent choice!"
- Knowledge disclaimers: "As of my last update", "While details are limited"

## Filler Phrases to CUT:
- "In order to" → "To"
- "Due to the fact that" → "Because"
- "At this point in time" → "Now"
- "Has the ability to" → "Can"
- "It is important to note that" → just state it

## ADD SOUL (not just pattern avoidance):
- Have actual opinions. "This one's a daily driver for a reason" beats neutral feature lists
- Vary rhythm. Short punchy. Then longer when detail matters.
- Acknowledge tradeoffs honestly. "Not the flashiest piece, but it does the job without breaking"
- Use specific scenarios over generic benefits. "When you're three dabs deep and don't want to think about technique"
- First person is fine sparingly. "We've seen these survive drops that would shatter glass"

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

# SEO HYPERLINKING STRATEGY (REQUIRED)

## Link Density Rules:
- Internal links: 2-5 per description (roughly per 1,000 words)
- External links: 1-2 per description (to authoritative non-competitor sources)
- Minimum spacing: 200+ characters between links
- First-mention rule: Link on the first natural mention of a topic, not every mention
- NEVER link to the same collection more than once per description

## Primary Collections (Oil Slick - always available):
- Dab Pads & Mats: https://oilslickpad.com/collections/dabbing
- Parchment Papers: https://oilslickpad.com/collections/parchment-papers
- Concentrate Jars: https://oilslickpad.com/collections/concentrate-jars
- Glass Jars: https://oilslickpad.com/collections/glass-jars-extract-packaging
- Silicone Rigs: https://oilslickpad.com/collections/silicone-smoking-devices
- Nectar Collectors: https://oilslickpad.com/collections/nectar-collectors-straws
- Dab Tools: https://oilslickpad.com/collections/dab-tools-dabbers
- Carb Caps: https://oilslickpad.com/collections/carb-caps

## Secondary Collections (Link when product/context is relevant):
DABBING:
- /collections/dab-rigs → trigger words: dab rig, oil rig, concentrate rig
- /collections/quartz-bangers → trigger words: quartz banger, banger, quartz nail
- /collections/carb-caps → trigger words: carb cap, directional cap, bubble cap
- /collections/dab-tools-dabbers → trigger words: dab tool, dabber
- /collections/nectar-collectors-straws → trigger words: nectar collector, honey straw, dab straw
- /collections/torches → trigger words: torch, butane torch, dab torch

SMOKING:
- /collections/silicone-smoking-devices → trigger words: bong, water pipe, glass bong
- /collections/silicone-pipes → trigger words: hand pipe, glass pipe, spoon pipe
- /collections/bubblers → trigger words: bubbler
- /collections/grinders → trigger words: grinder, herb grinder

STORAGE:
- /collections/concentrate-jars → trigger words: concentrate container, dab container, wax container
- /collections/mylar-bags → trigger words: mylar bag, smell proof bag

ROLLING:
- /collections/rolling-papers → trigger words: rolling paper, papers
- /collections/rolling-papers-cones → trigger words: cone, pre-roll, pre-rolled cone

## Anchor Text Best Practices:
DO:
- Use the natural phrase as it appears in the sentence
- Vary anchor text (don't always use "glass jars" — use "concentrate jars" or "storage jars" sometimes)
- Keep anchors 2-4 words when possible
- Make the link contextually relevant to the surrounding sentence

DON'T:
- Use "click here" or "learn more" as anchor text
- Over-optimize with exact-match keywords repeatedly
- Link the same collection more than once per article
- Put links inside headings (H1, H2, H3, H4)

## External Links (1-2 per description):
Add 1-2 external links to authoritative non-competitor sources when relevant:
- Health/Safety: Leafly, NORML, health.gov sites
- Cannabis Science: academic journals, Leafly, Weedmaps
- Industry News: MJBizDaily, Cannabis Business Times

External link format: <a href="URL" rel="noopener noreferrer" target="_blank">anchor text</a>

## What NOT to Do:
- No keyword stuffing — Write for humans, not search engines
- No link clusters — Space links at least 200 characters apart
- No self-referential links — Don't link to the same product you're writing about
- No competitor links — Never link to other smoke shops or competing suppliers
- No orphaned anchor text — Every link should flow naturally in the sentence
- No over-linking — If you've already linked to glass jars, don't link to it again

## Link Placement:
- Keep opening paragraph (first 2-3 sentences) link-free — focus on product only
- Place links in: Benefits section, How-To section, FAQ answers
- One link per paragraph maximum

The Brand Context section below contains additional collection URLs.

# CLEANING ADVICE

Keep cleaning instructions short and generic:
- "Let it cool, then wipe off residue. For deeper cleans, soak in isopropyl alcohol, rinse and dry before reuse."
- Don't add extra steps or make it sound like a detailed how-to guide
- Keep it safe and simple

# RULES
- 1,200-1,600 words minimum — comprehensive descriptions perform better for SEO and AI search
- Never invent specs — only use what's in the product data
- HTML tags: h2, h3, p, ul, ol, li, strong, em, table, tr, th, td, a (for links)
- Dual units: "4.5 inches (114mm)"
- Include thorough FAQ sections (5-7 questions), detailed feature explanations, and proper category context

# LINKING CHECKLIST (verify before output):
- 2-5 internal links total, each to a DIFFERENT collection
- 1-2 external links to authoritative sources (Leafly, NORML, etc.) with rel="noopener noreferrer" target="_blank"
- Minimum 200 characters spacing between links
- Opening paragraph: NO links (focus on product)
- No links inside headings
- Anchor text: natural 2-4 word phrases, varied (not same anchor repeatedly)
- External links only to non-competitors (no other smoke shops)

# HUMANIZER CHECKLIST (verify before output):
- No AI vocabulary words (delve, crucial, tapestry, landscape, underscore, showcase, etc.)
- No significance inflation (testament, pivotal, indelible mark, etc.)
- No rule-of-three forcing
- Varied sentence rhythm (mix short and long)
- Specific scenarios over generic benefits
- Honest tradeoffs acknowledged
- No em dash overuse
- No sycophantic phrases

# DATA CLEANLINESS CHECKLIST (verify before output):
- SKUs/model numbers: ONLY in specs table, never in body paragraphs or headings
- Spelling: All obvious typos corrected (silicone, banger, quartz, parchment, terpenes)
- No raw data artifacts (product codes, internal references, database IDs)

# COPYRIGHT CHECKLIST (verify before output):
- NO trademarked character names (Grogu, Yoda, Pikachu, Mickey, Stitch, etc.)
- All character references replaced with creative alternatives
- Design described by appearance, not by character name
- "Inspired by" language used where applicable

# TITLE OPTIMIZATION CHECKLIST:
- Product type leads the title
- Key differentiator included (material, size, feature)
- Search-friendly terms used
- Under 60 characters when possible
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
    if IS_CLAUDE:
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
    if IS_CLAUDE:
        print(f"   Model: {CLAUDE_MODEL}")
    else:
        print(f"   Batch Model: {OPENAI_BATCH_MODEL} (GPT-5.1 not supported in Batch API)")
        print(f"   Realtime Model: {OPENAI_REALTIME_MODEL}")

    # Show content source mode
    source_desc = "Existing PDP (rewrite mode)" if CONTENT_SOURCE == "existing_pdp" else "Product Data (write from scratch)"
    print(f"📄 Content source: {source_desc}")

    # Supabase status
    if store.is_configured():
        print(f"💾 Supabase: connected ({store.SUPABASE_URL[:40]}...)")
    else:
        print(f"💾 Supabase: not configured (results saved to local files only)")


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


def _strip_html_tags(html):
    """Strip HTML tags for word counting."""
    return re.sub(r'<[^>]+>', ' ', html)


def is_pdp_optimized(body_html):
    """Score a product description to determine if it's already optimized.

    Uses fast heuristic checks (no AI calls) looking for structural
    markers that match our system's output format.

    Returns (is_optimized: bool, score: int, reason: str).
    """
    if not body_html:
        return False, 0, "no description"

    html = body_html.strip()
    text = _strip_html_tags(html)
    word_count = len(text.split())

    # Gate: under 400 words is never considered optimized
    if word_count < 400:
        return False, 0, f"too short ({word_count} words)"

    score = 0
    signals = []

    # 1. Structured headings (h2/h3 sections)
    h2_count = len(re.findall(r'<h2[\s>]', html, re.IGNORECASE))
    h3_count = len(re.findall(r'<h3[\s>]', html, re.IGNORECASE))
    if h2_count >= 2:
        score += 1
        signals.append(f"{h2_count} h2 headings")

    # 2. Internal links to oilslickpad.com collections
    internal_links = re.findall(r'href=["\']https?://oilslickpad\.com/collections/', html, re.IGNORECASE)
    if len(internal_links) >= 2:
        score += 1
        signals.append(f"{len(internal_links)} internal links")

    # 3. FAQ section (questions in headings, or "FAQ" heading)
    has_faq_heading = bool(re.search(r'<h[23][^>]*>.*?(FAQ|frequently|questions)', html, re.IGNORECASE))
    question_headings = len(re.findall(r'<h3[^>]*>[^<]*\?', html, re.IGNORECASE))
    if has_faq_heading or question_headings >= 3:
        score += 1
        signals.append(f"FAQ section ({question_headings} questions)")

    # 4. Specs table
    if '<table' in html.lower():
        score += 1
        signals.append("specs table")

    # 5. Bullet lists (structured benefits/features)
    list_count = len(re.findall(r'<[uo]l[\s>]', html, re.IGNORECASE))
    if list_count >= 1:
        score += 1
        signals.append(f"{list_count} lists")

    # Threshold: 3 out of 5 signals + word count gate = optimized
    is_optimized = score >= 3
    reason = f"{word_count}w, score {score}/5 ({', '.join(signals)})" if signals else f"{word_count}w, score 0/5"

    return is_optimized, score, reason


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

    # Skip already-optimized PDPs
    if SKIP_OPTIMIZED:
        before_count = len(filtered)
        kept = []
        skipped_examples = []
        for p in filtered:
            html = p.get("body_html") or ""
            optimized, score, reason = is_pdp_optimized(html)
            if optimized:
                if len(skipped_examples) < 5:
                    skipped_examples.append(f"    {p.get('title', '?')[:50]} ({reason})")
            else:
                kept.append(p)
        skipped = before_count - len(kept)
        filtered = kept
        print(f"  ✓ Skip optimized: {skipped} already done, {len(filtered)} need work")
        if skipped_examples:
            print(f"  Skipped examples:")
            for ex in skipped_examples:
                print(ex)

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

    options = product.get('options', [])
    options_str = ", ".join([o.get('name', '') for o in options]) if options else "None"

    variants = product.get('variants', [])
    variants_summary = [f"{v.get('title', '')}: ${v.get('price', '0')}" for v in variants[:5]]
    variants_str = "; ".join(variants_summary) if variants_summary else "Single variant"

    # MODE: existing_pdp - Use the existing description as the primary source
    if CONTENT_SOURCE == "existing_pdp":
        if not body_html or len(body_html.strip()) < 100:
            # Fall back to product_data mode if no existing description
            return build_product_prompt_from_data(product, title, product_type, tags, vendor, options_str, variants_str)

        return f"""REWRITE AND IMPROVE this existing product description.

The existing description contains all the product details you need. Your job is to:
1. Rewrite it using our brand voice and humanizer guidelines
2. Expand thin sections and add proper FAQ (5-7 questions)
3. Apply SEO hyperlinking strategy (2-5 internal links, 1-2 external)
4. Fix any spelling errors, remove SKUs from body copy
5. Replace any copyrighted character names with safe alternatives
6. Optimize the title for SEO if needed

PRODUCT METADATA:
Title: {title}
Vendor: {vendor if vendor else 'Oil Slick'}
Type: {product_type}
Tags: {tags}
Options: {options_str}
Variants: {variants_str}

EXISTING DESCRIPTION TO REWRITE:
{body_html}

Return JSON only: {{"ai_body_html": "..."}}"""

    # MODE: product_data - Write from scratch using raw product data
    else:
        return build_product_prompt_from_data(product, title, product_type, tags, vendor, options_str, variants_str, body_html)


def build_product_prompt_from_data(product, title, product_type, tags, vendor, options_str, variants_str, body_html=''):
    """Build prompt from raw product data (original behavior)."""
    # Truncate existing description if too long (just for reference)
    if body_html and len(body_html) > 1000:
        body_html = body_html[:1000] + "..."

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
    if IS_CLAUDE:
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

    batch_id = response.json()["id"]

    # Verify batch is actually processing
    print("\n⏳ STEP 5: Verifying batch started processing...")
    verify_batch_started(batch_id, is_claude=False)

    return batch_id


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

    batch_data = response.json()
    batch_id = batch_data["id"]

    # Verify batch is actually processing
    print("\n⏳ STEP 4: Verifying batch started processing...")
    verify_batch_started(batch_id, is_claude=True)

    return batch_id


def verify_batch_started(batch_id, is_claude=False, max_attempts=10, delay=3):
    """Poll the batch status to verify it actually started processing."""
    for attempt in range(max_attempts):
        time.sleep(delay)

        if is_claude:
            response = requests.get(
                f"https://api.anthropic.com/v1/messages/batches/{batch_id}",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01"
                }
            )
        else:
            response = requests.get(
                f"https://api.openai.com/v1/batches/{batch_id}",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
            )

        if response.status_code != 200:
            print(f"   Attempt {attempt + 1}/{max_attempts}: Waiting for batch to initialize...")
            continue

        batch = response.json()

        if is_claude:
            status = batch.get("processing_status", "unknown")
            counts = batch.get("request_counts", {})
            total = counts.get("processing", 0) + counts.get("succeeded", 0) + counts.get("errored", 0)
            errored = counts.get("errored", 0)

            print(f"   Attempt {attempt + 1}: Status = {status}, Processing = {counts.get('processing', 0)}, Total tracked = {total}")

            if status == "ended":
                if errored > 0 or total == 0:
                    print(f"\n❌ BATCH FAILED IMMEDIATELY!")
                    print(f"   Status: {status}")
                    print(f"   Request counts: {json.dumps(counts, indent=2)}")
                    if batch.get("results_url"):
                        print(f"\n   Downloading error details...")
                        download_and_show_batch_errors(batch_id, is_claude=True)
                    exit(1)
                else:
                    print(f"   ✓ Batch completed quickly (small batch)")
                    return True
            elif status == "in_progress":
                print(f"   ✓ Batch confirmed processing!")
                return True
            elif status in ["canceling", "canceled"]:
                print(f"\n❌ Batch was canceled!")
                exit(1)
        else:
            status = batch.get("status", "unknown")
            counts = batch.get("request_counts", {})
            errors = batch.get("errors", {})

            print(f"   Attempt {attempt + 1}: Status = {status}")

            if status == "failed":
                print(f"\n❌ BATCH FAILED!")
                print(f"   Errors: {json.dumps(errors, indent=2) if errors else 'No error details'}")
                if batch.get("error_file_id"):
                    print(f"\n   Downloading error details...")
                    download_and_show_batch_errors(batch_id, is_claude=False)
                exit(1)
            elif status == "expired":
                print(f"\n❌ Batch expired before processing!")
                exit(1)
            elif status in ["in_progress", "finalizing"]:
                print(f"   ✓ Batch confirmed processing!")
                return True
            elif status == "validating":
                print(f"   Still validating...")
                continue
            elif status == "completed":
                print(f"   ✓ Batch completed quickly (small batch)")
                return True

    print(f"\n⚠️ Could not confirm batch started after {max_attempts} attempts.")
    print(f"   This doesn't mean it failed - check status manually with: python batch_processor.py status")
    return False


def download_and_show_batch_errors(batch_id, is_claude=False, max_errors=10):
    """Download and display batch errors for debugging."""
    if is_claude:
        # Get results URL and download
        response = requests.get(
            f"https://api.anthropic.com/v1/messages/batches/{batch_id}",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            }
        )
        if response.status_code != 200:
            print(f"   Could not fetch batch details: {response.status_code}")
            return

        batch = response.json()
        results_url = batch.get("results_url")

        if not results_url:
            print(f"   No results URL available yet.")
            print(f"   Full batch response: {json.dumps(batch, indent=2)}")
            return

        # Download results
        results_response = requests.get(
            results_url,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            }
        )

        if results_response.status_code != 200:
            print(f"   Could not download results: {results_response.status_code}")
            return

        print(f"\n{'='*70}")
        print(f"BATCH ERROR DETAILS (first {max_errors}):")
        print('='*70)

        error_count = 0
        for line in results_response.text.strip().split('\n'):
            if error_count >= max_errors:
                break
            try:
                result = json.loads(line)
                if result.get("result", {}).get("type") == "errored":
                    error_count += 1
                    custom_id = result.get("custom_id", "unknown")
                    error = result.get("result", {}).get("error", {})
                    print(f"\n  Product: {custom_id}")
                    print(f"  Error Type: {error.get('type', 'N/A')}")
                    print(f"  Message: {error.get('message', 'N/A')}")
            except:
                pass

        if error_count == 0:
            print("   No explicit errors found in results.")
            print(f"   Sample result: {results_response.text[:500]}")
        print('='*70)
    else:
        # OpenAI error file download
        response = requests.get(
            f"https://api.openai.com/v1/batches/{batch_id}",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
        )
        if response.status_code != 200:
            print(f"   Could not fetch batch details: {response.status_code}")
            return

        batch = response.json()
        error_file_id = batch.get("error_file_id")

        if not error_file_id:
            print(f"   No error file available.")
            print(f"   Batch errors: {json.dumps(batch.get('errors', {}), indent=2)}")
            return

        error_response = requests.get(
            f"https://api.openai.com/v1/files/{error_file_id}/content",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
        )

        print(f"\n{'='*70}")
        print(f"BATCH ERROR DETAILS (first {max_errors}):")
        print('='*70)

        for i, line in enumerate(error_response.text.strip().split('\n')):
            if i >= max_errors:
                break
            try:
                error_data = json.loads(line)
                custom_id = error_data.get("custom_id", "unknown")
                error = error_data.get("error", {})
                print(f"\n  Product: {custom_id}")
                print(f"  Code: {error.get('code', 'N/A')}")
                print(f"  Message: {error.get('message', 'N/A')}")
            except:
                print(f"   Raw: {line[:200]}")
        print('='*70)


def submit_batch():
    """Create and submit a batch job."""
    check_env()
    print("\n📦 STEP 1: Fetching products from Shopify...")
    products = get_shopify_products()

    if not products:
        print("❌ No products found matching your filters!")
        return

    provider_name = "Claude" if IS_CLAUDE else "OpenAI"

    if IS_CLAUDE:
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
        if failed > 0:
            print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ⚠️  COMPLETED WITH ERRORS                                        ║
║  → {failed} requests failed - check error details below           ║
║  → Run 'preview' to see successful results                       ║
╚══════════════════════════════════════════════════════════════════╝
""")
            # Show error details
            download_and_show_batch_errors(batch_id, is_claude=is_claude)
        else:
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
    elif status == "failed":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ❌ BATCH FAILED!                                                 ║
║  → The entire batch failed to process                            ║
║  → Check error details below and resubmit                        ║
╚══════════════════════════════════════════════════════════════════╝
""")
        # Show error details
        download_and_show_batch_errors(batch_id, is_claude=is_claude)
    elif status == "expired":
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ⚠️  BATCH EXPIRED!                                               ║
║  → OpenAI batches expire after 24 hours                          ║
║  → Please run 'submit' to create a new batch                     ║
╚══════════════════════════════════════════════════════════════════╝
""")
    elif status in ["canceled", "canceling"]:
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ⛔ BATCH CANCELED                                                ║
║  → The batch was canceled before completion                      ║
║  → Please run 'submit' to create a new batch                     ║
╚══════════════════════════════════════════════════════════════════╝
""")
    else:
        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  ❓ Unknown status: {status:<44} ║
║  → Raw batch data printed below for debugging                    ║
╚══════════════════════════════════════════════════════════════════╝
""")
        print(f"\nRaw batch response:\n{json.dumps(batch_info.get('raw', {}), indent=2)}")

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
    if IS_CLAUDE:
        return process_single_product_claude(product, total_count)
    else:
        return process_single_product_openai(product, total_count)


def update_shopify_product(product_id, body_html):
    """Update a single product's description on Shopify. Returns True on success."""
    if DRY_RUN:
        return True

    if len(body_html) < 100:
        return False

    try:
        url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products/{product_id}.json"
        headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}
        payload = {"product": {"id": int(product_id), "body_html": body_html}}

        response = requests.put(url, headers=headers, json=payload)
        time.sleep(0.5)  # Shopify rate limit

        return response.status_code == 200
    except Exception:
        return False


def process_and_apply_single_product(product, total_count):
    """Process a single product and immediately push to Shopify."""
    result = process_single_product(product, total_count)

    if result["success"] and result["body_html"]:
        shopify_success = update_shopify_product(result["product_id"], result["body_html"])
        result["shopify_updated"] = shopify_success
    else:
        result["shopify_updated"] = False

    return result


def realtime_process(auto_apply=False):
    """Process all products using parallel real-time API calls."""
    global progress_count
    progress_count = 0

    check_env()

    # Generate a unique run ID for this batch
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]

    print(f"\n📦 STEP 1: Fetching products from Shopify...")
    products = get_shopify_products()

    if not products:
        print("❌ No products to process.")
        return

    total = len(products)
    model_name = CLAUDE_MODEL if IS_CLAUDE else OPENAI_REALTIME_MODEL
    provider_name = "Claude" if IS_CLAUDE else "OpenAI"

    # Build a lookup of original descriptions for rollback tracking
    original_html = {str(p["id"]): (p.get("body_html") or "") for p in products}

    # Register run in Supabase
    if store.is_configured():
        store.create_run(
            run_id=run_id,
            model_provider=MODEL_PROVIDER,
            model_name=model_name,
            content_source=CONTENT_SOURCE,
            product_filter=PRODUCT_FILTER,
            total_products=total,
        )
        print(f"💾 Run ID: {run_id}")

    # Check if auto-apply is enabled (via parameter or env var)
    do_auto_apply = auto_apply or AUTO_APPLY

    print(f"\n🚀 STEP 2: Processing {total} products with {provider_name} (parallel)...")
    print(f"   Model: {model_name}")
    print(f"   Workers: {MAX_WORKERS} concurrent requests")
    if do_auto_apply:
        print(f"   🔄 Auto-apply: ENABLED (pushing to Shopify as generated)")
    else:
        print(f"   Auto-apply: disabled (run 'apply' after to push to Shopify)")
    print(f"   Estimated time: {total * 3 / MAX_WORKERS / 60:.1f} minutes\n")

    results = []
    success_count = 0
    error_count = 0
    shopify_updated_count = 0
    shopify_failed_count = 0

    start_time = time.time()

    # Choose the processing function based on auto-apply mode
    process_fn = process_and_apply_single_product if do_auto_apply else process_single_product

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_fn, p, total): p for p in products}

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result["success"]:
                success_count += 1
                if do_auto_apply:
                    if result.get("shopify_updated"):
                        shopify_updated_count += 1
                    else:
                        shopify_failed_count += 1
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

    # Persist to Supabase
    if store.is_configured():
        print(f"\n💾 STEP 3: Saving {len(results)} results to Supabase...")
        db_rows = []
        for r in results:
            pid = r["product_id"]
            before = original_html.get(pid, "")
            row = {
                "run_id": run_id,
                "shopify_product_id": int(pid),
                "product_title": r.get("title", ""),
                "body_html_before": before if before else None,
                "body_html_after": r.get("body_html", ""),
                "word_count": len(r.get("body_html", "").split()),
                "status": "applied" if (do_auto_apply and r.get("shopify_updated")) else ("generated" if r["success"] else "error"),
                "error_message": r.get("error"),
            }
            if do_auto_apply and r.get("shopify_updated"):
                row["applied_at"] = datetime.now(timezone.utc).isoformat()
            db_rows.append(row)

        # Insert in batches of 500 to avoid payload limits
        for i in range(0, len(db_rows), 500):
            chunk = db_rows[i:i + 500]
            store.save_descriptions_batch(chunk)

        store.update_run(run_id,
            succeeded=success_count,
            failed=error_count,
            status="applied" if do_auto_apply else "generated",
        )
        print(f"   ✓ Saved to Supabase (run: {run_id})")

    # Generate preview HTML
    step_num = "4" if store.is_configured() else "3"
    print(f"\n📄 STEP {step_num}: Generating preview report...")
    preview_results_data = []
    with open(RESULTS_FILE) as f:
        for line in f:
            preview_results_data.append(json.loads(line))

    html = generate_preview_html(preview_results_data, min(SAMPLE_COUNT, len(preview_results_data)))
    with open(PREVIEW_FILE, "w") as f:
        f.write(html)

    run_id_display = run_id[:30] + "..." if len(run_id) > 30 else run_id

    if do_auto_apply:
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║         ✅ REALTIME PROCESSING + SHOPIFY UPDATE COMPLETE         ║
╠══════════════════════════════════════════════════════════════════╣
║  Run ID:      {run_id_display:<50} ║
║  Provider:    {provider_name:<50} ║
║  Model:       {model_name:<50} ║
║  Products:    {total:<50} ║
║  AI Success:  {success_count:<50} ║
║  AI Failed:   {error_count:<50} ║
║  Time:        {elapsed/60:.1f} minutes{' ':<43} ║
╠══════════════════════════════════════════════════════════════════╣
║  🛒 SHOPIFY UPDATES:                                             ║
║  ✓ Updated:   {shopify_updated_count:<50} ║
║  ✗ Failed:    {shopify_failed_count:<50} ║
╠══════════════════════════════════════════════════════════════════╣
║  📄 Preview report: {PREVIEW_FILE:<44} ║
║  💾 Supabase:  results saved for rollback                        ║
║  ↩️  To rollback: run_id={run_id_display:<38} ║
╚══════════════════════════════════════════════════════════════════╝
""")
    else:
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║              ✅ REALTIME PROCESSING COMPLETE                     ║
╠══════════════════════════════════════════════════════════════════╣
║  Run ID:      {run_id_display:<50} ║
║  Provider:    {provider_name:<50} ║
║  Model:       {model_name:<50} ║
║  Products:    {total:<50} ║
║  Successful:  {success_count:<50} ║
║  Failed:      {error_count:<50} ║
║  Time:        {elapsed/60:.1f} minutes{' ':<43} ║
╠══════════════════════════════════════════════════════════════════╣
║  📄 Preview report: {PREVIEW_FILE:<44} ║
║  💾 Supabase:  results saved                                     ║
║  ✅ Run 'apply' with run_id to push to Shopify                   ║
╚══════════════════════════════════════════════════════════════════╝
""")

    print(f"RUN_ID: {run_id}")


def apply_results():
    """Apply saved results to Shopify. Pulls from Supabase if no local file."""
    check_env()

    # Determine the run to apply
    run_id = TARGET_RUN_ID
    descriptions = []

    # Strategy: try Supabase first, fall back to local file
    if store.is_configured():
        if not run_id:
            run_id = store.get_latest_run_id()
            if run_id:
                print(f"💾 Using latest Supabase run: {run_id}")
            else:
                print("💾 No runs found in Supabase.")

        if run_id:
            descriptions = store.get_descriptions(run_id, status="generated")
            if descriptions:
                print(f"💾 Loaded {len(descriptions)} descriptions from Supabase")

    # Fall back to local JSONL if Supabase had nothing
    if not descriptions and Path(RESULTS_FILE).exists():
        print(f"📄 Loading results from local file ({RESULTS_FILE})...")
        with open(RESULTS_FILE) as f:
            for line in f:
                row = json.loads(line)
                product_id = row.get("custom_id")
                content = row.get("response", {}).get("body", {}).get("choices", [{}])[0].get("message", {}).get("content", "{}")
                parsed = json.loads(content)
                descriptions.append({
                    "shopify_product_id": int(product_id),
                    "body_html_after": parsed.get("ai_body_html", ""),
                })

    if not descriptions:
        print("❌ No results found. Run 'realtime' first, or provide a run_id.")
        return

    total = len(descriptions)
    print(f"\n🔄 Applying {total} descriptions to Shopify...")

    if DRY_RUN:
        print("🔒 DRY RUN MODE - Not updating Shopify")
        return

    success_count = 0
    error_count = 0
    skip_count = 0

    for i, desc in enumerate(descriptions):
        try:
            product_id = str(desc["shopify_product_id"])
            body_html = desc.get("body_html_after", "")

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
                if store.is_configured() and run_id:
                    store.mark_applied(run_id, int(product_id))
            else:
                error_count += 1

            time.sleep(0.5)  # Shopify rate limit

        except Exception:
            error_count += 1

    # Update run status in Supabase
    if store.is_configured() and run_id:
        store.update_run(run_id, status="applied", succeeded=success_count, failed=error_count)

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                      ✅ SHOPIFY UPDATED                          ║
╠══════════════════════════════════════════════════════════════════╣
║  ✓ Successfully updated:  {success_count:<42} ║
║  ⚠️ Skipped (too short):   {skip_count:<42} ║
║  ✗ Errors:                {error_count:<42} ║
╠══════════════════════════════════════════════════════════════════╣
║  💾 Run ID:               {(run_id or 'local file'):<42} ║
╚══════════════════════════════════════════════════════════════════╝
""")


def rollback_run():
    """Rollback a run: restore original descriptions to Shopify."""
    if not store.is_configured():
        print("❌ Supabase not configured. Rollback requires Supabase.")
        print("   Set SUPABASE_URL and SUPABASE_KEY environment variables.")
        return

    check_env()

    run_id = TARGET_RUN_ID
    if not run_id:
        run_id = store.get_latest_run_id()
        if run_id:
            print(f"💾 Rolling back latest run: {run_id}")
        else:
            print("❌ No runs found in Supabase. Provide a run_id.")
            return

    # Fetch applied descriptions that have a before snapshot
    descriptions = store.get_descriptions(run_id, status="applied")
    if not descriptions:
        print(f"❌ No applied descriptions found for run {run_id}.")
        return

    rollback_candidates = [d for d in descriptions if d.get("body_html_before")]
    if not rollback_candidates:
        print(f"❌ No rollback data available (no before-snapshots stored).")
        return

    total = len(rollback_candidates)
    print(f"\n↩️  Rolling back {total} products to their original descriptions...")

    if DRY_RUN:
        print("🔒 DRY RUN MODE - Not updating Shopify")
        for d in rollback_candidates[:5]:
            print(f"   Would restore: {d['product_title']} ({d['shopify_product_id']})")
        return

    success_count = 0
    error_count = 0

    for i, desc in enumerate(rollback_candidates):
        try:
            product_id = str(desc["shopify_product_id"])
            original_html = desc["body_html_before"]

            if (i + 1) % 50 == 0:
                print(f"  Restoring {i+1}/{total}...")

            url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/products/{product_id}.json"
            headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}
            payload = {"product": {"id": int(product_id), "body_html": original_html}}

            response = requests.put(url, headers=headers, json=payload)

            if response.status_code == 200:
                success_count += 1
                store.mark_rolled_back(run_id, int(product_id))
            else:
                error_count += 1

            time.sleep(0.5)

        except Exception:
            error_count += 1

    store.update_run(run_id, status="rolled_back")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                     ↩️  ROLLBACK COMPLETE                         ║
╠══════════════════════════════════════════════════════════════════╣
║  Run ID:              {run_id[:40]:<42} ║
║  ✓ Restored:          {success_count:<42} ║
║  ✗ Errors:            {error_count:<42} ║
║  Products now have their original descriptions.                  ║
╚══════════════════════════════════════════════════════════════════╝
""")


def show_history():
    """Show recent batch runs from Supabase."""
    if not store.is_configured():
        print("❌ Supabase not configured. History requires Supabase.")
        print("   Set SUPABASE_URL and SUPABASE_KEY environment variables.")
        return

    runs = store.list_runs(limit=10)

    if not runs:
        print("📋 No runs found in Supabase.")
        return

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                     📋 RUN HISTORY                               ║
╠══════════════════════════════════════════════════════════════════╣""")

    for run in runs:
        rid = run["run_id"][:30]
        status = run["status"]
        model = run.get("model_name", "?")[:20]
        total = run.get("total_products", 0)
        ok = run.get("succeeded", 0)
        fail = run.get("failed", 0)
        created = run.get("created_at", "")[:19]
        source = run.get("content_source", "product_data")

        status_icon = {
            "generating": "⏳",
            "generated": "📦",
            "applied": "✅",
            "rolled_back": "↩️ ",
        }.get(status, "❓")

        print(f"║  {status_icon} {rid:<31} {created}     ║")
        print(f"║     {model:<22} {source:<15} {ok}/{total} ok  {status:<14} ║")
        print(f"╠──────────────────────────────────────────────────────────────────╣")

    print(f"╚══════════════════════════════════════════════════════════════════╝")
    print(f"\n  To apply a run:   RUN_ID=<id> python batch_processor.py apply")
    print(f"  To rollback:      RUN_ID=<id> python batch_processor.py rollback")


def main():
    parser = argparse.ArgumentParser(description="Parallel Product Description Generator")
    parser.add_argument("command", choices=["submit", "status", "preview", "download", "realtime", "realtime-live", "apply", "rollback", "history"])
    args = parser.parse_args()

    # Determine provider info for headers
    provider_name = f"Claude ({CLAUDE_MODEL})" if IS_CLAUDE else "GPT-5.1"

    if args.command == "realtime":
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           Using {provider_name} Real-Time API (parallel){' '*(21-len(provider_name))}║
╚══════════════════════════════════════════════════════════════════╝
        """)
        realtime_process(auto_apply=False)
    elif args.command == "realtime-live":
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           Using {provider_name} Real-Time + Auto-Push to Shopify{' '*(14-len(provider_name))}║
╚══════════════════════════════════════════════════════════════════╝
        """)
        realtime_process(auto_apply=True)
    elif args.command == "apply":
        print("""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           Applying Results to Shopify                            ║
╚══════════════════════════════════════════════════════════════════╝
        """)
        apply_results()
    elif args.command == "rollback":
        print("""
╔══════════════════════════════════════════════════════════════════╗
║        🚀 Parallel Product Description Generator                 ║
║           ↩️  Rolling Back to Original Descriptions                ║
╚══════════════════════════════════════════════════════════════════╝
        """)
        rollback_run()
    elif args.command == "history":
        show_history()
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
