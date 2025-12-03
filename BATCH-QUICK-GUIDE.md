# Batch Processing Quick Guide

A simple 3-step guide to creating AI product descriptions in batch and pushing them to Shopify.

---

## Prerequisites

Make sure you have these environment variables set:

```bash
# Required
export SHOPIFY_STORE="your-store.myshopify.com"
export SHOPIFY_ACCESS_TOKEN="shpat_xxxxx"
export ANTHROPIC_API_KEY="sk-ant-xxxxx"   # For Claude (default)
# OR
export OPENAI_API_KEY="sk-xxxxx"           # For OpenAI
```

---

## Step 1: Create a Batch (Submit)

This fetches your products from Shopify and sends them to the AI for processing.

### Using Command Line:

```bash
cd batch-processing
python batch_processor.py submit
```

### Using GitHub Actions:

1. Go to **Actions** tab in your repository
2. Select **"Batch Product Descriptions"** workflow
3. Click **"Run workflow"**
4. Set `action` to **`submit`**
5. Click **Run workflow**

### What happens:
- Products are fetched from Shopify
- A batch job is created with the AI provider
- A `batch_id.txt` file is saved with your batch ID
- Cost estimate: ~$0.015-0.025 per product

**Note:** Batch processing takes **1-4 hours** to complete (but costs 50% less than real-time).

---

## Step 2: Check Batch Status

Monitor your batch until it's complete.

### Using Command Line:

```bash
cd batch-processing
python batch_processor.py status
```

### Using GitHub Actions:

1. Run workflow with `action` set to **`status`**

### What you'll see:

```
📊 Batch Status: in_progress
   Completed: 45/100 (45%)
```

When complete, you'll see:

```
✅ Batch Status: completed
   Completed: 100/100 (100%)
   Ready to preview or download!
```

**Tip:** Run status every 30-60 minutes until complete.

---

## Step 3: Preview Results (Optional but Recommended)

See the generated descriptions before pushing to Shopify.

### Using Command Line:

```bash
cd batch-processing
python batch_processor.py preview
```

### Using GitHub Actions:

1. Run workflow with `action` set to **`preview`**

### What you get:
- Sample descriptions shown in console
- `preview_report.html` - Full visual report
- `batch_results.jsonl` - All raw results

---

## Step 4: Push to Shopify (Download & Apply)

Apply all generated descriptions to your Shopify products.

### Using Command Line:

```bash
cd batch-processing
python batch_processor.py download
```

### Using GitHub Actions:

1. Run workflow with `action` set to **`download`**

### What happens:
- Descriptions are validated (minimum 100 characters)
- Each product is updated in Shopify
- Rate-limited to avoid API throttling
- Final report shows success/fail counts

### Dry Run (Test without updating):

```bash
DRY_RUN=true python batch_processor.py download
```

Or in GitHub Actions, check the **"Dry run"** checkbox.

---

## Quick Reference

| Step | Command | GitHub Action |
|------|---------|---------------|
| Create batch | `python batch_processor.py submit` | action: `submit` |
| Check status | `python batch_processor.py status` | action: `status` |
| Preview results | `python batch_processor.py preview` | action: `preview` |
| Push to Shopify | `python batch_processor.py download` | action: `download` |

---

## Alternative: Real-Time Processing

For smaller batches or when you need results immediately (costs more, but no wait):

```bash
# Process products in real-time
python batch_processor.py realtime

# Apply results to Shopify
python batch_processor.py apply
```

---

## Filtering Products

You can filter which products to process:

```bash
# Only products with empty descriptions
PRODUCT_FILTER=needs_description python batch_processor.py submit

# Only specific product type
PRODUCT_FILTER=by_type FILTER_PRODUCT_TYPE="Bongs" python batch_processor.py submit

# Only specific vendor
PRODUCT_FILTER=by_vendor FILTER_VENDOR="Grav Labs" python batch_processor.py submit

# Limit for testing
PRODUCT_LIMIT=10 python batch_processor.py submit
```

---

## Troubleshooting

**Batch expired?**
- OpenAI batches expire after 24 hours. Submit a new batch.

**No batch_id found?**
- Make sure you ran `submit` first
- Check that `batch_id.txt` exists in the batch-processing folder

**Shopify rate limited?**
- The script has built-in delays. Just wait and retry.

**Descriptions too short?**
- Descriptions under 100 characters are skipped automatically

---

## Cost Estimates

| Products | Batch API (50% off) | Real-time |
|----------|---------------------|-----------|
| 10 | ~$0.15 | ~$0.30 |
| 100 | ~$1.50 | ~$3.00 |
| 500 | ~$7.50 | ~$15.00 |
| 1000 | ~$15.00 | ~$30.00 |

*Prices are estimates based on Claude Sonnet 4.5*
