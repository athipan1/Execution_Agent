from app.services.order_review_approval_ticket import (
    build_order_review_approval_ticket,
)


def _ticket(plans):
    return build_order_review_approval_ticket({"plans": plans})


def test_blocked_ticket_has_blocked_status_and_requires_attention():
    ticket = _ticket(
        [
            {
                "symbol": "ACGL",
                "preview_status": "blocked_missing_reference_price",
            }
        ]
    )

    assert ticket["ticket_status"] == "blocked"
    assert ticket["requires_operator_attention"] is True
    assert ticket["summary"]["requires_operator_attention"] is True


def test_ready_ticket_has_ready_status_and_requires_attention():
    ticket = _ticket(
        [
            {
                "symbol": "BKNG",
                "preview_status": "ready_for_manual_review",
                "position_qty": "51",
                "current_stop_order": {"id": "stop-bkng"},
            }
        ]
    )

    assert ticket["ticket_status"] == "ready_for_manual_approval"
    assert ticket["requires_operator_attention"] is True
    assert ticket["approval_required"] is True


def test_no_action_ticket_is_clean_and_requires_no_attention():
    ticket = _ticket(
        [
            {
                "symbol": "CINF",
                "preview_status": "no_action_required",
            }
        ]
    )

    assert ticket["ticket_status"] == "no_action_required"
    assert ticket["requires_operator_attention"] is False
    assert ticket["approval_required"] is False


def test_empty_ticket_is_explicitly_empty():
    ticket = _ticket([])

    assert ticket["ticket_status"] == "empty"
    assert ticket["requires_operator_attention"] is False
