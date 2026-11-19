"""A subclass can give the start-up sign-in its own connection retries (issue #135).

The agent signs in at start-up with fewer connection retries than the session keeps afterwards. A subclass sets
``_startup_connect_retries``; when it is None (the default) the configured policy is used for everything.
"""

from pyhocon import ConfigFactory

from cairnml.backend_api.session.session import Session

CONFIGURED_CONNECT_RETRIES = 7


def _session(startup_connect_retries=None) -> Session:
    session_class = type(
        "StartupSession",
        (Session,),
        {"_startup_connect_retries": startup_connect_retries},
    )
    session = session_class.__new__(session_class)
    session._Session__http_retries_config = {
        "total": 0,
        "connect": CONFIGURED_CONNECT_RETRIES,
        "config": ConfigFactory.from_dict({}),
    }
    session._Session__http_session = session._Session__new_http_session()
    return session


def _connect_retries(session: Session) -> int:
    return session._Session__http_session.get_adapter(
        "https://api.example"
    ).max_retries.connect


def test_default_sign_in_at_start_up_keeps_the_configured_retries(monkeypatch):
    session = _session()
    seen = []
    monkeypatch.setattr(
        Session,
        "refresh_token",
        lambda self, *a, **k: seen.append(_connect_retries(self)),
    )

    session._sign_in_at_startup()

    assert seen == [CONFIGURED_CONNECT_RETRIES]
    assert _connect_retries(session) == CONFIGURED_CONNECT_RETRIES


def test_subclass_sign_in_at_start_up_uses_its_own_connect_retries_only_for_that_sign_in(
    monkeypatch,
):
    session = _session(startup_connect_retries=4)
    seen = []
    monkeypatch.setattr(
        Session,
        "refresh_token",
        lambda self, *a, **k: seen.append(_connect_retries(self)),
    )

    session._sign_in_at_startup()

    assert seen == [4]
    assert _connect_retries(session) == CONFIGURED_CONNECT_RETRIES
    assert (
        session._Session__http_retries_config["connect"] == CONFIGURED_CONNECT_RETRIES
    )


def test_the_start_up_retries_are_restored_when_the_sign_in_fails(monkeypatch):
    session = _session(startup_connect_retries=4)

    def refuse(self, *a, **k):
        raise ConnectionError("no answer")

    monkeypatch.setattr(Session, "refresh_token", refuse)

    try:
        session._sign_in_at_startup()
    except ConnectionError:
        pass

    assert _connect_retries(session) == CONFIGURED_CONNECT_RETRIES
