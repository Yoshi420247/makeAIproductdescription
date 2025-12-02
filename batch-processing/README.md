# Parallel Product Description Generator — OpenAI Batch API

Process **hundreds or thousands** of product descriptions simultaneously using OpenAI's Batch API.

## Why Batch API?

| Benefit | Details |
|---------|---------|
| **50% Cost Savings** | Half the price of synchronous API calls |
| **No Rate Limits** | Separate quota from regular API |
| **True Parallelism** | All requests processed simultaneously |
| **24h Completion** | Usually much faster depending on load |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SCENARIO 1: SUBMIT BATCH                     │
├─────────────────────────────────────────────────────────────────┤
│  Shopify          Text           OpenAI         OpenAI          │
│  Get All    →   Aggregator  →   Upload    →   Create           │
│  Products       (JSONL)         File          Batch Job         │
│                                                    │             │
│                                              Save batch_id       │
└─────────────────────────────────────────────────────────────────┘
                              ↓ (wait for completion)
┌─────────────────────────────────────────────────────────────────┐
│                   SCENARIO 2: PROCESS RESULTS                    │
├─────────────────────────────────────────────────────────────────┤
│  Check           Download        Parse          Shopify         │
│  Batch      →    Results    →   JSONL     →   Update           │
│  Status          File           Iterator       Products         │
└─────────────────────────────────────────────────────────────────┘
```

## Files Included

1. `scenario-1-submit-batch.json` — Make.com scenario to export products and submit batch
2. `scenario-2-process-results.json` — Make.com scenario to import results
3. `batch-request-template.jsonl` — Example JSONL format for batch requests

## Setup Instructions

### 1. Import Scenarios to Make.com
- Go to Make.com → Scenarios → Import
- Import both JSON files
- Connect your Shopify and OpenAI accounts

### 2. Configure Scenario 1 (Submit Batch)
- Set your Shopify connection
- Adjust product filters if needed (status, type, etc.)
- Run manually or schedule (e.g., weekly)

### 3. Configure Scenario 2 (Process Results)
- Schedule to run every 30 minutes
- Or trigger via webhook when batch completes
- Will automatically update Shopify when batch is done

## Batch API Limits

- **Max requests per batch**: 50,000
- **Max file size**: 200 MB
- **Completion window**: 24 hours (often faster)
- **Concurrent batches**: Unlimited

## Cost Comparison (1,000 products)

| Method | Time | Cost (est.) |
|--------|------|-------------|
| Sequential API | ~83 hours | $50 |
| Batch API | ~2-4 hours | **$25** |

## JSONL Request Format

Each line in the batch file:
```json
{"custom_id": "product_123", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "gpt-5.1", "messages": [...], "response_format": {"type": "json_object"}}}
```

## Monitoring

Check batch status in OpenAI Dashboard:
https://platform.openai.com/batches

Or via API:
```bash
curl https://api.openai.com/v1/batches/{batch_id} \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```
