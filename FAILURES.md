# System Failure Modes and Limitations

This document outlines the failure scenarios, limits, and recovery characteristics of this backend service. These limits are inherent to distributed systems, SQLite persistence, and interactions with external, unreliable third-party APIs.

---

### 1. Unavoidable External API Race Boundary (Comment Deletion during Send)
* **Scenario**: 
  1. The `dm_worker` queries the queue, locks a job, updates its status to `'sending'`, and starts the asynchronous network call `POST /v1/dm/send`.
  2. While the HTTP request is in-flight, a `comment.deleted` webhook arrives.
  3. The `matching_worker` attempts to process the deletion. It searches for jobs with `comment_id` in `'queued'` state to cancel.
* **Failure Impact**: Because the job is already in `'sending'` state, the deletion worker cannot cancel it. The external API completes the delivery, and the user receives the DM despite deleting their comment.
* **Technical Cause**: A network call to an external service is a non-transactional, side-effect-producing operation that cannot be aborted mid-flight. 
* **Mitigation**: The system minimizes the race window by atomically updating the database status to `'sending'` immediately before starting the network request. Once the request is in-flight, the race boundary is crossed.

---

### 2. Process Crash During Send Call (Mitigated by Startup Crash Recovery)
* **Scenario**: The `dm_worker` transitions a job to `'sending'`, logs it in `DMAttemptLog`, and executes the API request. Before a response is received, the application container is suddenly restarted or killed.
* **Failure Impact**: On startup, any jobs that were in `'sending'` state would normally remain stuck forever (since workers only fetch `'queued'` jobs).
* **Mitigation (Implemented)**: On application startup, the `lifespan` handler executes `recover_stuck_jobs()`. This function automatically queries the database for any jobs stuck in `'sending'` status beyond the configured `STUCK_JOB_TIMEOUT_SECONDS` (default: 300 seconds), logs the recovery, and reverts them back to `'queued'` so they can be picked up by the worker again. It retains the same stable `Idempotency-Key` (`dm-job-{job_id}`) so that the retry does not cause duplicate sends at the mock API level.

---

### 3. External API Outage Outlasting Retry Policy
* **Scenario**: The external PseudoGram API goes offline completely for several hours.
* **Failure Impact**: Eligible jobs will fail to send, trigger retries, and back off exponentially (1s, 2s, 4s, 8s...). Once a job's attempts count reaches `MAX_DM_ATTEMPTS` (5 attempts, totaling ~15 seconds of backoff), the job status is permanently set to `'failed'`. When the external API comes back online, these messages remain `'failed'` and are lost unless manually re-queued.
* **Technical Cause**: The retry loop has a finite ceiling to prevent infinite retries from hogging system resources and bloat.
* **Mitigation**: Implement an administrative dashboard or command-line utility to bulk-reset jobs in `'failed'` status back to `'queued'` once the external API has recovered.

---

### 4. SQLite Writer Lockouts Under Multi-Process Deployments
* **Scenario**: The application is deployed on a multi-core server running multiple Uvicorn worker processes (or multiple container instances sharing a network mount database).
* **Failure Impact**: During peak load (like our 500-event stress test), multiple processes concurrently try to write to the SQLite database. One or more processes will raise a `sqlite3.OperationalError: database is locked` error.
* **Technical Cause**: Although WAL (Write-Ahead Logging) mode allows multiple readers concurrently, SQLite only supports a single writer at any given time. While we use `BEGIN IMMEDIATE` to serialize writes within a connection, concurrent database filesystems or other OS-level file locks will cause write lockouts.
* **Mitigation**: SQLite is ideal for single-process architectures. For multi-process or multi-instance deployments, the database connection settings in `config.py` should be updated to target a client-server database like PostgreSQL or MySQL.

---

### 5. Reconciliation Failures (Stuck in 'sent_queued' due to External API State Loss)
* **Scenario**: The worker successfully sends a DM, receives an HTTP 202, and records the `dm_id`. However, the external PseudoGram API experiences a database partition or state loss and permanently loses the record of that `dm_id`.
* **Failure Impact**: Subsequent calls to `GET /v1/dm/{dm_id}` return a 404 error or remain `'queued'` forever. The reconciliation worker polls this ID up to `MAX_RECONCILIATION_POLLS` (10 times) and then transitions the job permanently to `'failed'`. If the external API recovers the state afterward, the backend will still reflect a status of `'failed'`.
* **Technical Cause**: Reconciliation relies on the external system maintaining a stable, consistent state for the lifecycle of the transaction.
