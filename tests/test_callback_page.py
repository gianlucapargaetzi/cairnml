from cairnml.idp.callback_page import page


def test_the_page_is_self_contained_html_with_the_logo():
    html = page("Signed in", "You can close this tab.").decode()
    assert "<h1>Signed in</h1>" in html
    assert "data:image/svg+xml;base64," in html
    assert "http://" not in html and "https://" not in html.replace("https://www.w3.org", "")


def test_texts_from_the_identity_provider_are_escaped():
    html = page("Sign-in not completed", "x", ok=False, detail="<script>alert(1)</script>").decode()
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
