# Step-by-Step Setup Guide

Process **hundreds of products in parallel** using OpenAI's Batch API. Choose your preferred method below.

---

## Quick Comparison

| Method | Difficulty | Cost | Best For |
|--------|------------|------|----------|
| **GitHub Actions** | ⭐ Easy | Free | Most users, automated runs |
| **Local Machine** | ⭐ Easy | Free | Quick one-time runs |
| **Railway** | ⭐⭐ Medium | $5/mo | Always-on, scheduled runs |
| **Vercel** | ❌ Not recommended | - | Time limits too short |

---

## Prerequisites (All Methods)

### 1. Get Your OpenAI API Key
1. Go to [platform.openai.com](https://platform.openai.com)
2. Click your profile → **View API Keys**
3. Click **Create new secret key**
4. Copy and save it (starts with `sk-`)

### 2. Get Your Shopify Access Token
1. Go to your Shopify Admin
2. **Settings** → **Apps and sales channels** → **Develop apps**
3. Click **Create an app** → Name it "AI Descriptions"
4. Click **Configure Admin API scopes**
5. Enable these scopes:
   - `read_products`
   - `write_products`
6. Click **Save** → **Install app**
7. Click **Reveal token once** and copy it (starts with `shpat_`)

### 3. Your Shopify Store URL
Format: `your-store-name.myshopify.com`
Example: `oil-slick-pad.myshopify.com`

---

## Option 1: GitHub Actions (Recommended) ⭐

**Best for:** Automated scheduled runs, free, no server needed

### Step 1: Fork/Clone This Repository
```bash
git clone https://github.com/YOUR_USERNAME/makeAIproductdescription.git
cd makeAIproductdescription
```

### Step 2: Add Secrets to GitHub
1. Go to your repo on GitHub
2. **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** and add:

| Name | Value |
|------|-------|
| `OPENAI_API_KEY` | `sk-your-key-here` |
| `SHOPIFY_STORE` | `your-store.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | `shpat_your-token-here` |

### Step 3: Run the Workflow
1. Go to **Actions** tab in your repo
2. Click **Generate Product Descriptions (Batch)**
3. Click **Run workflow**
4. Select action: `submit`
5. Click **Run workflow** (green button)

### Step 4: Check Status (after 1-4 hours)
1. Go to **Actions** → **Run workflow**
2. Select action: `status`
3. View the output to see progress

### Step 5: Download Results (when complete)
1. Go to **Actions** → **Run workflow**
2. Select action: `download`
3. Your Shopify products will be updated!

### Optional: Schedule Automatic Runs
Edit `.github/workflows/batch-descriptions.yml` and uncomment:
```yaml
schedule:
  - cron: '0 6 * * 1'  # Every Monday at 6 AM UTC
```

---

## Option 2: Run Locally ⭐

**Best for:** Quick one-time runs, testing

### Step 1: Install Python
Download from [python.org](https://python.org) (version 3.8+)

### Step 2: Clone and Setup
```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/makeAIproductdescription.git
cd makeAIproductdescription/batch-processing

# Install dependencies
pip install requests
```

### Step 3: Set Environment Variables

**Mac/Linux:**
```bash
export OPENAI_API_KEY="sk-your-key-here"
export SHOPIFY_STORE="your-store.myshopify.com"
export SHOPIFY_ACCESS_TOKEN="shpat_your-token-here"
```

**Windows (Command Prompt):**
```cmd
set OPENAI_API_KEY=sk-your-key-here
set SHOPIFY_STORE=your-store.myshopify.com
set SHOPIFY_ACCESS_TOKEN=shpat_your-token-here
```

**Windows (PowerShell):**
```powershell
$env:OPENAI_API_KEY="sk-your-key-here"
$env:SHOPIFY_STORE="your-store.myshopify.com"
$env:SHOPIFY_ACCESS_TOKEN="shpat_your-token-here"
```

### Step 4: Run the Script
```bash
# Submit batch (creates jobs for all products)
python batch_processor.py submit

# Check status (run after 1-4 hours)
python batch_processor.py status

# Download and apply results (when status shows "completed")
python batch_processor.py download
```

---

## Option 3: Railway ⭐⭐

**Best for:** Always-on server, scheduled runs, easy deployment

### Step 1: Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub

### Step 2: Deploy
1. Click **New Project** → **Deploy from GitHub repo**
2. Select your `makeAIproductdescription` repo
3. Railway will auto-detect Python

### Step 3: Add Environment Variables
1. Click on your service
2. Go to **Variables** tab
3. Add:
   - `OPENAI_API_KEY` = your key
   - `SHOPIFY_STORE` = your-store.myshopify.com
   - `SHOPIFY_ACCESS_TOKEN` = your token

### Step 4: Run Commands
1. Go to **Settings** → **Deploy**
2. Set start command to:
```bash
python batch-processing/batch_processor.py submit
```

### Step 5: Schedule with Cron (Optional)
Add a `railway.json` file:
```json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python batch-processing/batch_processor.py submit",
    "cronSchedule": "0 6 * * 1"
  }
}
```

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1: SUBMIT (run once)                                      │
│  ───────────────────────                                        │
│  • Fetches all products from Shopify                            │
│  • Creates JSONL file with GPT-5.1 prompts                      │
│  • Uploads to OpenAI                                            │
│  • Creates batch job                                            │
│  • Saves batch_id for tracking                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    Wait 1-4 hours
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STEP 2: STATUS (run to check)                                  │
│  ────────────────────────────                                   │
│  • Checks batch progress                                        │
│  • Shows completed/total count                                  │
│  • Tells you when ready                                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    When status = "completed"
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STEP 3: DOWNLOAD (run when ready)                              │
│  ─────────────────────────────────                              │
│  • Downloads all AI-generated descriptions                     │
│  • Updates each Shopify product                                 │
│  • Reports success/error counts                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Cost Estimate

| Products | OpenAI Cost | Time |
|----------|-------------|------|
| 100 | ~$2.50 | 1-2 hours |
| 500 | ~$12.50 | 2-4 hours |
| 1,000 | ~$25 | 3-6 hours |

**Note:** Batch API is **50% cheaper** than regular API calls!

---

## Troubleshooting

### "Missing environment variables"
Make sure all three variables are set:
- `OPENAI_API_KEY`
- `SHOPIFY_STORE`
- `SHOPIFY_ACCESS_TOKEN`

### "Shopify API error: 401"
Your access token is invalid or expired. Generate a new one in Shopify Admin.

### "Batch status: failed"
Check the error message. Common issues:
- Invalid model name (use `gpt-5.1` or `gpt-4.1`)
- API key doesn't have Batch API access
- File format issues

### "No products found"
Make sure:
- Your store URL is correct (include `.myshopify.com`)
- You have active, published products
- Your access token has `read_products` scope

---

## Files Reference

```
makeAIproductdescription/
├── .github/
│   └── workflows/
│       └── batch-descriptions.yml    # GitHub Actions workflow
├── batch-processing/
│   ├── batch_processor.py            # Main Python script
│   ├── README.md                     # Technical docs
│   └── scenario-*.json               # Make.com alternatives
├── make-automation-gpt5.1.json       # Single-product Make.com automation
├── optimized-make-automation.json    # Previous version
└── SETUP-GUIDE.md                    # This file
```

---

## Need Help?

- **OpenAI Batch API Docs:** [platform.openai.com/docs/guides/batch](https://platform.openai.com/docs/guides/batch)
- **Shopify API Docs:** [shopify.dev/docs/api/admin-rest](https://shopify.dev/docs/api/admin-rest)
- **GitHub Actions Docs:** [docs.github.com/en/actions](https://docs.github.com/en/actions)
