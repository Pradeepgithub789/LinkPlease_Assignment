import asyncio
import random
import time
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime
from app.config import settings
from app.workers.matching_worker import run_matching_worker
from app.workers.dm_worker import run_dm_worker
from app.workers.reconciliation_worker import run_reconciliation_worker
from app.models import Rule, WebhookEvent, DMJob, DuplicatesBlockedEvent
from app.database import SessionLocal

@pytest.mark.asyncio
async def test_500_event_simulation(client, db_session):
    # 1. Setup faster configuration parameters for simulation scale
    settings.TESTING = True
    settings.RATE_LIMIT_WINDOW_SECONDS = 0.5  # 10 sends per 0.5 seconds
    settings.WORKER_POLL_INTERVAL_SECONDS = 0.05
    settings.RECONCILIATION_INTERVAL_SECONDS = 0.05
    settings.WEBHOOK_SIGNATURE_REQUIRED = False  # Disable signature check for mock load speed

    # Create rule in DB
    rule = Rule(id="rule-promo", keyword="PROMO", dm_message="Here is your promo code!")
    db_session.add(rule)
    db_session.commit()

    # 2. Mock external client calls with a stateful simulator
    send_calls = []
    dm_status_store = {}

    async def mock_send_dm(recipient_user_id, message, comment_id, idempotency_key):
        now = time.time()
        send_calls.append(now)
        
        # Verify rate limiting is respected by the application:
        # Check window: count how many calls in the last 0.5 seconds
        cutoff = now - 0.5
        recent_sends = [t for t in send_calls if t >= cutoff]
        
        # If application exceeded rate limits, fail the test immediately!
        if len(recent_sends) > 10:
            raise AssertionError(f"Rate limit exceeded! Sent {len(recent_sends)} requests in last 0.5 seconds.")
            
        dm_id = f"dm_sim_{len(send_calls)}"
        dm_status_store[dm_id] = "delivered"  # Simulate instant delivery
        
        return 202, {"dm_id": dm_id, "status": "queued"}, {}

    async def mock_get_status(dm_id):
        status = dm_status_store.get(dm_id, "queued")
        return 200, {"dm_id": dm_id, "status": status}

    # 3. Start background worker loops manually
    worker_matching = asyncio.create_task(run_matching_worker())
    
    # We patch the client calls inside worker loops
    with patch("app.workers.dm_worker.client.send_dm", side_effect=mock_send_dm) as _, \
         patch("app.workers.reconciliation_worker.client.get_dm_status", side_effect=mock_get_status) as _:
         
        worker_dm = asyncio.create_task(run_dm_worker())
        worker_recon = asyncio.create_task(run_reconciliation_worker())

        # 4. Generate 500 webhook events distributed over 10 seconds
        # - 200 Unique creations matching rules (usr_1 to usr_200)
        # - 100 Duplicate event_ids
        # - 100 Business duplicates (same user commenting again)
        # - 100 Non-matching comments
        events_payload = []
        
        # Unique valid events
        for i in range(1, 201):
            events_payload.append({
                "event_id": f"evt_valid_{i}",
                "event_type": "comment.created",
                "sent_at": datetime.utcnow().isoformat(),
                "data": {
                    "comment_id": f"cmt_valid_{i}",
                    "text": "Get PROMO now!",
                    "from": {"user_id": f"usr_{i}", "username": f"user.{i}"}
                }
            })
            
        # Duplicate event IDs (first 100)
        for i in range(1, 101):
            events_payload.append({
                "event_id": f"evt_valid_{i}", # Same event_id
                "event_type": "comment.created",
                "sent_at": datetime.utcnow().isoformat(),
                "data": {
                    "comment_id": f"cmt_valid_{i}",
                    "text": "Get PROMO now!",
                    "from": {"user_id": f"usr_{i}", "username": f"user.{i}"}
                }
            })

        # Business duplicates (same user commenting again)
        for i in range(1, 101):
            events_payload.append({
                "event_id": f"evt_bus_dup_{i}",
                "event_type": "comment.created",
                "sent_at": datetime.utcnow().isoformat(),
                "data": {
                    "comment_id": f"cmt_bus_dup_{i}",
                    "text": "PROMO please!",
                    "from": {"user_id": f"usr_{i}", "username": f"user.{i}"} # Same user
                }
            })

        # Non-matching comments
        for i in range(1, 101):
            events_payload.append({
                "event_id": f"evt_nomatch_{i}",
                "event_type": "comment.created",
                "sent_at": datetime.utcnow().isoformat(),
                "data": {
                    "comment_id": f"cmt_nomatch_{i}",
                    "text": "Just saying hello",
                    "from": {"user_id": f"usr_nomatch_{i}", "username": f"user.nomatch.{i}"}
                }
            })

        # Shuffle to simulate random arrivals
        random.shuffle(events_payload)

        # Send concurrently over 10 seconds
        latencies = []

        async def send_event(payload):
            # Calculate sleep duration to spread over 10 seconds
            delay = random.uniform(0, 10)
            await asyncio.sleep(delay)
            
            start_time = time.time()
            response = client.post("/webhook", json=payload)
            latencies.append(time.time() - start_time)
            
            assert response.status_code == 200

        # Run webhooks concurrent tasks
        await asyncio.gather(*(send_event(p) for p in events_payload))

        # Latency Assertions: Webhook should be extremely fast
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        print(f"\nWebhook Latency: Average={avg_latency:.4f}s, Max={max_latency:.4f}s")
        assert avg_latency < 0.1, f"Average webhook latency too high: {avg_latency}s"
        assert max_latency < 0.5, f"Maximum webhook latency too high: {max_latency}s"

        # 5. Wait for background workers to drain the queue.
        # Since we have 200 unique eligible users and limit is 10 sends / 0.5s,
        # it should take at least 200/10*0.5 = 10 seconds to finish processing all DMs.
        # We poll stats until 'sent' reaches 200.
        timeout = time.time() + 25.0
        success = False
        while time.time() < timeout:
            stats_resp = client.get("/stats")
            stats_data = stats_resp.json()
            print(f"Stats check: {stats_data}")
            if stats_data["sent"] == 200 and stats_data["queued"] == 0:
                success = True
                break
            await asyncio.sleep(0.5)

        # Clean up workers
        worker_matching.cancel()
        worker_dm.cancel()
        worker_recon.cancel()
        await asyncio.gather(worker_matching, worker_dm, worker_recon, return_exceptions=True)

        assert success, "Workers failed to process all 200 DMs within the timeout limit."

        # 6. Verify final statistics
        stats_resp = client.get("/stats")
        final_stats = stats_resp.json()
        
        assert final_stats["sent"] == 200
        assert final_stats["failed"] == 0
        assert final_stats["queued"] == 0
        assert final_stats["duplicates_blocked"] == 100
