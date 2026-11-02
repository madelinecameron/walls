import json
from datetime import timedelta

import httpx
import pytest

from tests.conftest import NOW
from tests.test_store import vault
from walls import digest, store

MONDAY_6AM = NOW.replace(hour=6)


class FakeMailbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message_id": f"m{len(self.sent)}"})


@pytest.fixture
def mailbox() -> FakeMailbox:
    return FakeMailbox()


@pytest.fixture
def http(mailbox: FakeMailbox) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(mailbox))


async def seed(db, ai, settings, times, **overrides):
    for _ in range(times):
        await store.report_wall(db, ai, settings, vault(**overrides), NOW)


async def test_sends_once_per_week_to_the_configured_address(db, ai, settings, mailbox, http):
    await seed(db, ai, settings, 1)

    assert await digest.run_if_due(db, ai, settings, http, NOW) == "m1"
    assert await digest.run_if_due(db, ai, settings, http, NOW) is None
    assert len(mailbox.sent) == 1
    assert mailbox.sent[0]["to"] == [settings.digest_to]
    assert mailbox.sent[0]["type"] == "DIGEST"

    assert await digest.run_if_due(db, ai, settings, http, NOW + timedelta(days=7)) == "m2"


async def test_not_before_monday_morning(db, ai, settings, mailbox, http):
    await seed(db, ai, settings, 1)
    assert await digest.run_if_due(db, ai, settings, http, MONDAY_6AM) is None
    assert mailbox.sent == []


async def test_nothing_is_sent_before_the_first_report(db, ai, settings, mailbox, http):
    assert await digest.run_if_due(db, ai, settings, http, NOW) is None
    assert mailbox.sent == []


async def test_a_failed_send_is_tried_again(db, ai, settings, mailbox, http):
    await seed(db, ai, settings, 1)
    mailbox.down = True
    with pytest.raises(httpx.HTTPStatusError):
        await digest.run_if_due(db, ai, settings, http, NOW)

    mailbox.down = False
    assert await digest.run_if_due(db, ai, settings, http, NOW) == "m1"


async def test_only_recurring_walls_get_a_model_suggestion(db, ai, settings, litellm):
    await seed(db, ai, settings, 3)
    await seed(db, ai, settings, 2, action_needed="Press the reset button", system="printer")

    assert await digest.refresh_remedies(db, ai, settings, NOW) == 1
    assert len(litellm.remedy_calls) == 1
    assert "Rotate the Vault token" in litellm.remedy_calls[0]


async def test_suggestion_is_rewritten_only_after_the_wall_doubles(db, ai, settings, litellm):
    await seed(db, ai, settings, 3)
    await digest.refresh_remedies(db, ai, settings, NOW)

    await seed(db, ai, settings, 2)
    assert await digest.refresh_remedies(db, ai, settings, NOW) == 0

    await seed(db, ai, settings, 1)
    assert await digest.refresh_remedies(db, ai, settings, NOW) == 1
    assert len(litellm.remedy_calls) == 2


async def test_unusable_model_answer_does_not_stop_the_digest(
    db, ai, settings, litellm, mailbox, http
):
    await seed(db, ai, settings, 3)
    litellm.remedy_answer = "I cannot help with that"

    assert await digest.run_if_due(db, ai, settings, http, NOW) == "m1"
    assert "->" not in mailbox.sent[0]["body"]["needs_you"][0]


async def test_digest_body_shows_rank_fix_and_flags(db, ai, settings):
    await seed(db, ai, settings, 3)
    near = "Rotate the Vault token and restart core services"
    await seed(db, ai, settings, 1, action_needed=near)
    await seed(db, ai, settings, 1, blocker_type="human_judgment")
    await digest.refresh_remedies(db, ai, settings, NOW)

    body = digest.compose(await digest.collect(db, settings, NOW))

    assert body["subject"] == "Walls: 2 open, 3 new, 5 reports this week"
    assert body["needs_you"][0].startswith("#1 3x [credential] vault: Rotate the Vault token")
    assert "-> permission, small: Grant renew" in body["needs_you"][0]
    assert any("possible duplicates" in line for line in body["heads_up"])
    assert any("questions for you" in line for line in body["heads_up"])


async def test_every_item_fits_mailbox_limits(db, ai, settings):
    await seed(db, ai, settings, 1, action_needed="é" * 500)
    body = digest.compose(await digest.collect(db, settings, NOW))
    assert all(len(item.encode()) <= digest.ITEM_LIMIT for item in body["needs_you"])


async def test_a_quiet_week_says_so(db, ai, settings):
    await seed(db, ai, settings, 1)
    later = NOW + timedelta(days=14)
    body = digest.compose(await digest.collect(db, settings, later))
    assert any("No reports this week" in line for line in body["heads_up"])
