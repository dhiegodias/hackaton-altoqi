import pytest

from radar import service


def test_audit_pages_reach_old_events_and_remain_stable_when_new_events_arrive(
    database, api_client
):
    with database() as conn:
        lead = service.capture(
            conn, {"company": "Auditoria Fictícia", "consultant": "Teste"}, "audit-person"
        )
        for number in range(237):
            service.audit(conn, lead["id"], "approved", {"sequence": number}, "Revisora fictícia")

    first = api_client.get("/api/audit/page").json()
    assert first["total"] == 238 and first["pages"] == 10
    assert [event["detail"]["sequence"] for event in first["items"]] == list(range(236, 211, -1))
    assert all(event["company"] == "Auditoria Fictícia" for event in first["items"])
    # New arrivals must not push events onto a page already visited.
    with database() as conn:
        service.audit(conn, None, "sources_updated", {"arrived_later": True})
    events = list(first["items"])
    for number in range(2, 11):
        response = api_client.get(
            "/api/audit/page", params={"page": number, "through_id": first["through_id"]}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 238
        events.extend(result["items"])
    assert len(events) == len({event["id"] for event in events}) == 238
    assert [event["detail"]["sequence"] for event in events[:-1]] == list(range(236, -1, -1))
    assert events[-1]["action"] == "created"
    refreshed = api_client.get("/api/audit/page").json()
    assert refreshed["total"] == 239 and refreshed["items"][0]["detail"]["arrived_later"]
    assert len(api_client.get("/api/audit").json()) == 200  # Existing consumers remain compatible.


def test_audit_empty_and_page_after_the_end(database, api_client):
    empty = api_client.get("/api/audit/page", params={"page": 8}).json()
    assert empty["items"] == [] and empty["total"] == 0 and empty["page"] == 1
    with database() as conn:
        for number in range(51):
            service.audit(conn, None, "sources_updated", {"sequence": number})
    result = api_client.get("/api/audit/page", params={"page": 99, "page_size": 50}).json()
    assert result["page"] == 2 and result["pages"] == 2
    assert len(result["items"]) == 1 and result["items"][0]["detail"]["sequence"] == 0
    original_empty = api_client.get(
        "/api/audit/page", params={"through_id": empty["through_id"]}
    ).json()
    assert original_empty["total"] == 0 and original_empty["items"] == []


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"through_id": -1}, {"through_id": 2**64}],
)
def test_audit_rejects_invalid_or_unbounded_pages(database, api_client, params):
    assert api_client.get("/api/audit/page", params=params).status_code == 422
