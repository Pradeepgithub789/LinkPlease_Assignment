# LinkPlease Backend Service

An automated comment-to-DM automation engine designed for Instagram-like platforms, built to handle unreliable networks, out-of-order webhooks, duplicate requests, and rate limits.

---

## 1. Project Overview
LinkPlease is a backend service that automates Instagram comment-to-DM interactions. When a creator registers a rule (e.g., keyword `"PRICE"` maps to a DM message), and a user leaves a comment containing that keyword, the backend automatically sends a DM to the commenter. 

---

## 2. System Architecture
The service utilizes a **FastAPI** web server combined with an asynchronous, database-backed queue and worker architecture:

```
                  [ Webhook Request ]
                           │
                           ▼
                  +─────────────────+
                  │  Fast Webhook   │
                  │   Ingestion     │
                  +────────┬────────+
                           │
                           ▼ (Deduplicate & Persist raw event)
                    [ SQLite DB ]
                           │
             ┌─────────────┼──────────────┐
             ▼             ▼              ▼
     +───────────────+ +────────────+ +──────────────────────+
     │ Matching      │ │ DM Worker  │ │ Reconciliation       │
     │ Worker        │ │            │ │ Worker               │
     +───────┬───────+ +─────┬──────+ +──────────┬───────────+
             │               │                   │
             ▼               ▼ (Idempotency Key) ▼
        [Evaluate]    [POST /dm/send]    [GET /dm/{id}]
             │               │                   │
             └───────────────┼───────────────────┘
                             ▼
                    +─────────────────+
                    │ PseudoGram API  │
                    +─────────────────+
```

1. **Webhook Ingestion**: High-throughput `/webhook` endpoint receives events, performs fast signature verification and event duplication checks, commits events to SQLite database as `'pending'`, and returns `200 OK` in milliseconds.
2. **Matching Worker**: Background worker loops through pending events:
   - Evaluates keyword matching (case-insensitive substring).
   - Creates a job in `dm_jobs` with status `'queued'`.
   - Handles comment deletions, including out-of-order arrival.
3. **DM Worker**: Queries `'queued'` jobs, reserves a rate limit slot atomically, updates the job status to `'sending'` inside an exclusive write-transaction, and dispatches the DM using the stable key `dm-job-{job_id}`.
4. **Reconciliation Worker**: Polls `GET /v1/dm/{dm_id}` for jobs in `'sent_queued'` until they are confirmed `'delivered'` or `'failed'`.

---

## 3. Technology Stack
- **Runtime**: Python 3.11+
- **Framework**: FastAPI (asyncio)
- **Database**: SQLite (local development and tests) & PostgreSQL (production)
- **ORM**: SQLAlchemy 2.0
- **HTTP Client**: httpx (async HTTP requests)
- **Validation**: Pydantic v2
- **Testing**: pytest & pytest-asyncio

---

## 4. Setup and Configuration

### Environment Variables
Configure the system by creating a `.env` file in the root directory:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `PSEUDOGRAM_API_KEY` | API Key for external PseudoGram client authentication. | *Required* |
| `PSEUDOGRAM_BASE_URL` | Base URL for the PseudoGram API. | `https://pseudogram-api.onrender.com` |
| `DATABASE_URL` | SQLAlchemy connection string. | `sqlite:///./linkplease.db` |
| `WEBHOOK_SIGNATURE_REQUIRED`| Toggle HMAC-SHA256 signature verification. | `true` |
| `MAX_DM_ATTEMPTS` | Max API request attempts before marking a job as failed. | `5` |
| `MAX_RECONCILIATION_POLLS` | Max times a job's delivery status will be checked. | `10` |

---

## 5. How to Run Locally

1. **Clone the repository** and navigate to the project directory:
   ```bash
   cd LinkPlease
   ```
2. **Create a virtual environment** and install dependencies:
   ```bash
   python -m venv venv
   source venv/Scripts/activate  # On Windows
   pip install -r requirements.txt
   ```
3. **Set up configurations**:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```
4. **Start the application**:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

---

## 6. API Endpoints

### 1. Register Rule
* **URL**: `POST /rules`
* **Request Body**:
  ```json
  {
    "keyword": "PRICE",
    "dm_message": "Here is the price list: http://example.com/pricing"
  }
  ```
* **Response (HTTP 201)**:
  ```json
  {
    "rule_id": "4b68df7e-52f9-4b62-8e1f-7301297e59f2",
    "keyword": "PRICE",
    "dm_message": "Here is the price list: http://example.com/pricing"
  }
  ```

### 2. Ingest Webhook Event
* **URL**: `POST /webhook`
* **Headers**: `X-PseudoGram-Signature: sha256=<hmac_hex>`
* **Request Body**:
  ```json
  {
    "event_id": "evt_01J8ZQ4K2N7RXA",
    "event_type": "comment.created",
    "sent_at": "2026-08-10T09:14:22.481Z",
    "data": {
      "comment_id": "cmt_9f2a7c",
      "post_id": "post_44de1b",
      "text": "PRICE please 🙏",
      "from": {
        "user_id": "usr_3b91fe",
        "username": "arjun.shoots"
      }
    }
  }
  ```
* **Response (HTTP 200)**:
  ```json
  {
    "status": "accepted"
  }
  ```

### 3. Retrieve Stats
* **URL**: `GET /stats`
* **Response (HTTP 200)**:
  ```json
  {
    "sent": 142,
    "failed": 3,
    "queued": 8,
    "duplicates_blocked": 57
  }
  ```

---

## 7. Database Design
Six tables are implemented inside SQLite:
1. `rules`: Stores registered automation rules (UUID `id`, `keyword`, and `dm_message`).
2. `webhook_events`: Stores incoming events (event_id primary key, type, comment metadata) to enforce event-level deduplication.
3. `dm_jobs`: Stores queued, sending, sent, cancelled, and failed DM tasks. Enforces active user-rule business deduplication.
4. `duplicates_blocked_events`: Records occurrences of duplicate comment blocks for persistent stats tracking.
5. `deleted_comments`: Tracks comment deletions to prevent processing out-of-order creation webhooks.
6. `dm_attempts_log`: Logs timestamps of sent requests to enforce the rolling 10 DMs / 60 seconds rate limit.

---

## 8. Core Strategies

### Duplicate Handling (Idempotency)
- **Event-Level Deduplication**: The `webhook_events` table enforces a primary key constraint on `event_id`. Duplicate incoming webhook events fail database insertion and are immediately ignored.
- **Business-Level Deduplication**: Enforces that a user never receives the same rule twice. A database-level partial unique index (supporting both SQLite and PostgreSQL) is declared on `dm_jobs(user_id, rule_id) WHERE status IN ('queued', 'sending', 'sent_queued', 'delivered')`.
  - This allows future valid comments to generate DMs if a prior job was `'cancelled'` or `'failed'`, while preventing race conditions from creating concurrent duplicate sends.
  - If a user/rule uniqueness violation is caught, a row is inserted in `duplicates_blocked_events` to track stats.

### Rate Limiting Strategy
- The PseudoGram API allows a maximum of **10 requests per rolling 60 seconds**.
- The `dm_worker` executes within an atomic rate-limiting reservation block:
  - For **SQLite**: It starts an exclusive write transaction using `BEGIN IMMEDIATE`.
  - For **PostgreSQL**: It obtains a transaction-level advisory lock using `SELECT pg_advisory_xact_lock(1337)`.
- It queries the `dm_attempts_log` table for attempts in the last 60 seconds. If count >= 10, it calculates the necessary sleep duration to clear the oldest log entry, rolls back the transaction (which automatically releases the lock), and sleeps. This check and slot reservation are completely atomic and safe across concurrent workers.

### Retry Policy
- **HTTP 500 / Network Timeout**: Job is retried using exponential backoff (`2 ** attempts` seconds). If attempts reach `MAX_DM_ATTEMPTS` (5), the job is marked `'failed'`.
- **HTTP 429 (Rate Limit)**: Reschedules the job back to `'queued'` state and sets `next_retry_at = now + Retry-After` using the header returned by the API.
- **HTTP 400 (Bad Request)**: Transitions job status to `'failed'` immediately without retrying.

### comment.deleted Handling
- When a `comment.deleted` event is processed, the worker looks up `dm_jobs` matching `comment_id`. If the job is `'queued'`, its status is changed to `'cancelled'`. 
- If the deletion arrives *before* the creation event (out-of-order), the `comment_id` is recorded in the `deleted_comments` table. When the creation event is processed later, the matching worker sees it is deleted and ignores it.

### Webhook Signature Verification
- Incoming webhooks contain the `X-PseudoGram-Signature` header.
- The raw request body is read and hashed using HMAC-SHA256 with `PSEUDOGRAM_API_KEY`.
- Hashing is verified before JSON parsing using constant-time `hmac.compare_digest`.

---

## 9. Verification & Testing

### How to Run Automated Tests
Execute the test suite using pytest:
```bash
python -m pytest
```

### Stress-Test Simulation (500 Events / 10 Seconds)
The system includes a 500-event stress test simulation located in [test_simulation.py](file:///c:/Users/paras/OneDrive/Documents/LP_Assign/tests/test_simulation.py).
- Spawns concurrent tasks simulating 500 webhook calls spread over 10 seconds.
- Verifies that `/webhook` average latency remains under **50ms**.
- Confirms rate limits are strictly respected, event/business duplicates are blocked, and stats aggregate accurately.

To run the simulation manually:
```bash
python -m pytest -s tests/test_simulation.py
```

---

## 10. Deployment Instructions (Render)

To deploy the service completely free on Render using Render Free PostgreSQL (since Render Free Web Services have an ephemeral filesystem and do not support persistent disks):

### Step 1: Create a Render PostgreSQL Database
1. Go to your Render Dashboard and click **New** -> **PostgreSQL**.
2. Set a name for the database (e.g., `linkplease-db`).
3. Select the **Free** tier.
4. Click **Create Database**.
5. Once created, copy the **Internal Database URL** (if the web service is in the same Render region) or the **External Database URL** (for external connections).

### Step 2: Create the Web Service
1. In the Render Dashboard, click **New** -> **Web Service**.
2. Connect your GitHub repository.
3. Set the following configuration:
   - **Name**: `linkplease-api`
   - **Runtime**: `Docker`
   - **Plan**: `Free`
4. Add the following **Environment Variables** in the Web Service configuration:
   - `PSEUDOGRAM_API_KEY`: `<your_pseudogram_api_key>`
   - `PSEUDOGRAM_BASE_URL`: `https://pseudogram-api.onrender.com`
   - `DATABASE_URL`: `<your_copied_postgres_database_url>` (Paste the connection URL copied in Step 1. SQLAlchemy will automatically normalize it if it starts with `postgres://` to `postgresql://`)
   - `WEBHOOK_SIGNATURE_REQUIRED`: `true`
5. Click **Deploy Web Service**. Render will build the Docker container and start the web server. The database tables will be automatically initialized at startup.
