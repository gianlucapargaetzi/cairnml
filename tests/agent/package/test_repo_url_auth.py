"""The address of a repository: the agent adds no user or password of its own (cairnml#194)."""

import pytest

from cairnml_agent.helper.repo import Git

ADDRESSES = [
    "https://bitbucket.org/company/repo",
    "https://user@bitbucket.org/company/repo",
    "https://user:password@bitbucket.org/company/repo",
    "ssh://git@bitbucket.org/company/repo",
    "git@bitbucket.org:company/repo.git",
]


@pytest.mark.parametrize("url", ADDRESSES)
def test_an_address_is_never_given_a_user_or_password(url):
    assert Git.add_auth(url) == url


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://bitbucket.org/company/repo", "https://bitbucket.org/company/repo"),
        ("https://user@bitbucket.org/company/repo", "https://bitbucket.org/company/repo"),
        ("https://user:password@bitbucket.org/company/repo", "https://bitbucket.org/company/repo"),
        ("ssh://git@bitbucket.org/company/repo", "ssh://git@bitbucket.org/company/repo"),
        ("git@bitbucket.org:company/repo.git", "git@bitbucket.org:company/repo.git"),
    ],
)
def test_the_user_and_password_of_a_web_address_can_be_taken_off(url, expected):
    assert Git.add_auth(url, reset_auth=True) == expected
