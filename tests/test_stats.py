from app.models import DMJob, DuplicatesBlockedEvent

def test_stats_aggregation_accuracy(client, db_session):
    # Insert jobs in different states
    jobs = [
        # Delivered (sent)
        DMJob(rule_id="r1", user_id="u1", comment_id="c1", message="m", status="delivered"),
        DMJob(rule_id="r1", user_id="u2", comment_id="c2", message="m", status="delivered"),
        # Failed
        DMJob(rule_id="r1", user_id="u3", comment_id="c3", message="m", status="failed"),
        # Queued states
        DMJob(rule_id="r1", user_id="u4", comment_id="c4", message="m", status="queued"),
        DMJob(rule_id="r1", user_id="u5", comment_id="c5", message="m", status="sending"),
        DMJob(rule_id="r1", user_id="u6", comment_id="c6", message="m", status="sent_queued"),
        # Cancelled states (should NOT count as queued, sent, or failed)
        DMJob(rule_id="r1", user_id="u7", comment_id="c7", message="m", status="cancelled"),
    ]
    db_session.add_all(jobs)
    db_session.commit()

    # Insert duplicates blocked
    dups = [
        DuplicatesBlockedEvent(event_id="e1", user_id="u10", rule_id="r1", comment_id="c10"),
        DuplicatesBlockedEvent(event_id="e2", user_id="u11", rule_id="r1", comment_id="c11"),
        DuplicatesBlockedEvent(event_id="e3", user_id="u12", rule_id="r1", comment_id="c12"),
    ]
    db_session.add_all(dups)
    db_session.commit()

    # Query stats
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    
    assert data["sent"] == 2
    assert data["failed"] == 1
    assert data["queued"] == 3
    assert data["duplicates_blocked"] == 3
