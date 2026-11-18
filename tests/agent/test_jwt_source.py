"""Token decoding uses the PyJWT package the agent requires, not a vendored copy of it."""

import cairnml_agent.backend_api.session.token_manager as token_manager


def test_the_token_manager_decodes_with_pyjwt_not_a_vendored_copy():
    assert token_manager.jwt.__name__ == "jwt"
    assert "_vendor" not in token_manager.jwt.__file__


def test_a_signed_token_round_trips_through_the_token_manager():
    import jwt

    token = jwt.encode({"exp": 4102444800, "identity": {"user": "u1"}}, "x" * 32, algorithm="HS256")
    claims = token_manager.TokenManager.get_decoded_token(token)
    assert claims["identity"] == {"user": "u1"}
    assert token_manager.TokenManager.get_decoded_token(token).get("exp") == 4102444800


def test_a_malformed_token_raises_the_error_the_session_catches():
    import jwt
    import pytest

    with pytest.raises(jwt.DecodeError):
        token_manager.TokenManager.get_decoded_token("not-a-token")
