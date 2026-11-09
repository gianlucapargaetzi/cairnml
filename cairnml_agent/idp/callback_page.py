"""The page the browser shows after the sign-in: the CairnML look of the sign-in page, in one piece (the listener on
127.0.0.1 serves no other files, and the page must not need the network)."""

import base64
from html import escape
from pathlib import Path

# The CairnML logo, stacked variant for dark backgrounds: the same file as the sign-in page of the identity provider.
_LOGO = base64.b64encode((Path(__file__).parent / "cairnml-logo-stacked-dark.svg").read_bytes()).decode()

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CairnML</title>
<style>
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 16px;
    font-family: 'Heebo', 'Roboto', 'Helvetica Neue', Arial, sans-serif; color: #151515;
    background: linear-gradient(135deg, #7d7d7d 0%, #4a4a4a 35%, #1c1c1c 75%, #000 100%) fixed;
  }
  img { display: block; width: 540px; max-width: 100%; height: auto; margin-bottom: 32px; }
  main { width: 540px; max-width: 100%; background: #fff; padding: 48px; box-shadow: 0 8px 16px rgba(0, 0, 0, .3); }
  .status { display: flex; align-items: center; gap: 16px; margin: 0 0 16px; }
  .icon { flex: none; width: 36px; height: 36px; border-radius: 50%; background: @COLOR@; color: #fff; display: flex;
          align-items: center; justify-content: center; font-size: 22px; font-weight: 700; line-height: 1; }
  h1 { margin: 0; font-size: 28px; font-weight: 500; }
  p { margin: 0; font-size: 16px; line-height: 1.5; }
  .detail { margin-top: 8px; color: #4d4d4d; font-size: 14px; }
</style>
</head>
<body>
<img alt="CairnML" src="data:image/svg+xml;base64,@LOGO64@">
<main>
  <div class="status"><span class="icon" aria-hidden="true">@ICON@</span><h1>@TITLE@</h1></div>
  <p>@TEXT@</p>@DETAIL@
</main>
</body>
</html>
"""


def page(title: str, text: str, *, ok: bool = True, detail: str = "") -> bytes:
    """The page as bytes. The texts are escaped: the detail can come from the address the identity provider called."""
    values = {
        "@COLOR@": "#0060A7" if ok else "#C9190B",
        "@ICON@": "&#10003;" if ok else "!",
        "@TITLE@": escape(title),
        "@TEXT@": escape(text),
        "@DETAIL@": f'<p class="detail">{escape(detail)}</p>' if detail else "",
        "@LOGO64@": _LOGO,
    }
    html = _PAGE
    for key, value in values.items():
        html = html.replace(key, value)
    return html.encode()
