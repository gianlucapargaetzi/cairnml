"""A queue tagged force_workers:off is not pulled from; any other queue is."""

from cairnml_agent.commands.worker import QUEUE_TAG_OFF, Worker


def test_a_queue_tagged_off_is_not_pulled_from():
    assert QUEUE_TAG_OFF == "force_workers:off"
    assert Worker._queue_is_pulled_from({"name": "q", "tags": [QUEUE_TAG_OFF]}) is False


def test_a_queue_without_the_tag_is_pulled_from():
    assert Worker._queue_is_pulled_from({"name": "q", "tags": ["other"]}) is True
    assert Worker._queue_is_pulled_from({"name": "q", "tags": ["force_workers:on"]}) is True
