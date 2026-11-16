"""Local fake tender website used by the crawler and end-to-end tests."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import urlparse


def make_pdf(lines: Iterable[str]) -> bytes:
    """Create a tiny one-page text PDF without third-party PDF writers."""

    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 12 Tf", "16 TL", "50 760 Td"]
    for line in lines:
        content_lines.append(f"({escape(line)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    )
    pdf.extend(b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(pdf)


GUNTUR_HTML = """<!doctype html>
<html><head><title>Guntur Electrical Tenders</title></head>
<body>
<h1>Guntur Electrical Works Tender</h1>
<table>
<tr><th>Tender Title</th><td>Supply and Installation of LED Street Lights</td></tr>
<tr><th>Tender Type</th><td>Electrical Works</td></tr>
<tr><th>Location</th><td>Guntur, Andhra Pradesh</td></tr>
<tr><th>Estimated Cost</th><td>Rs. 12,50,000</td></tr>
<tr><th>Timeline</th><td>90 days</td></tr>
<tr><th>Products/Scope</th><td>LED street light fittings, poles and installation</td></tr>
<tr><th>Department</th><td>Guntur Municipal Corporation</td></tr>
<tr><th>Last Date to Bid</th><td>31/10/2026</td></tr>
</table>
<nav>
<a href="/hyderabad-civil.html">Hyderabad civil tenders</a>
<a href="/guntur-housekeeping.pdf">Guntur housekeeping tender PDF</a>
</nav>
</body></html>
"""

HYDERABAD_JS_HTML = """<!doctype html>
<html><head><title>Hyderabad Civil Tenders</title></head>
<body>
<div id="app">Loading tenders...</div>
<script>
document.getElementById('app').innerHTML = `
  <h1>Hyderabad Road Widening Tender</h1>
  <div>Tender Type: Civil Works</div>
  <div>Location: Hyderabad, Telangana</div>
  <div>Budget: Rs. 2.4 crore</div>
  <div>Timeline: 18 months</div>
  <div>Scope: Road widening, drainage and footpath works</div>
  <div>Department: Hyderabad Metropolitan Development Authority</div>
  <div>Last Date to Bid: 15 November 2026</div>`;
</script>
</body></html>
"""

GUNTUR_PDF_LINES = [
    "Guntur Housekeeping Services Tender",
    "Tender Type: Housekeeping Services",
    "Location: Guntur, Andhra Pradesh",
    "Budget: INR 8,40,000",
    "Timeline: 12 months",
    "Products/Scope: Housekeeping and sanitation services",
    "Department: Andhra Pradesh Health Department",
    "Last Date to Bid: 05-11-2026",
]

NEW_SECURITY_HTML = """<!doctype html>
<html><head><title>Guntur Security Tenders</title></head>
<body>
<h1>Guntur Security Services Tender</h1>
<table>
<tr><th>Tender Title</th><td>Security Guards for District Offices</td></tr>
<tr><th>Tender Type</th><td>Security Services</td></tr>
<tr><th>Location</th><td>Guntur, Andhra Pradesh</td></tr>
<tr><th>Budget</th><td>Rs. 6,00,000</td></tr>
<tr><th>Timeline</th><td>6 months</td></tr>
<tr><th>Scope</th><td>Supply of trained security guards and supervision</td></tr>
<tr><th>Department</th><td>Guntur District Police</td></tr>
<tr><th>Last Date to Bid</th><td>20/12/2026</td></tr>
</table>
</body></html>
"""


class FakeSite:
    def __init__(self) -> None:
        self.pages: dict[str, tuple[str, bytes]] = {
            "/": ("text/html; charset=utf-8", GUNTUR_HTML.encode("utf-8")),
            "/hyderabad-civil.html": (
                "text/html; charset=utf-8",
                HYDERABAD_JS_HTML.encode("utf-8"),
            ),
            "/guntur-housekeeping.pdf": (
                "application/pdf",
                make_pdf(GUNTUR_PDF_LINES),
            ),
        }
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._stopped = False

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def add_page(self, path: str, body: str | bytes, content_type: str) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.pages[path] = (content_type, data)

    def add_security_page(self) -> None:
        self.add_page(
            "/guntur-security.html", NEW_SECURITY_HTML, "text/html; charset=utf-8"
        )

    def _home(self) -> bytes:
        links = []
        for path in sorted(self.pages):
            if path == "/":
                continue
            links.append(f'<li><a href="{path}">{path}</a></li>')
        return (
            "<!doctype html><html><head><title>Guntur Electrical Tenders</title>"
            "</head><body><h1>Guntur Electrical Works Tender</h1>"
            "<table>"
            "<tr><th>Tender Title</th><td>Supply and Installation of LED Street Lights</td></tr>"
            "<tr><th>Tender Type</th><td>Electrical Works</td></tr>"
            "<tr><th>Location</th><td>Guntur, Andhra Pradesh</td></tr>"
            "<tr><th>Estimated Cost</th><td>Rs. 12,50,000</td></tr>"
            "<tr><th>Timeline</th><td>90 days</td></tr>"
            "<tr><th>Products/Scope</th><td>LED street light fittings, poles and installation</td></tr>"
            "<tr><th>Department</th><td>Guntur Municipal Corporation</td></tr>"
            "<tr><th>Last Date to Bid</th><td>31/10/2026</td></tr>"
            "</table>"
            "<ul>" + "".join(links) + "</ul>"
            "</body></html>"
        ).encode("utf-8")

    def _handler(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    content_type = "text/plain"
                elif path == "/":
                    body = site._home()
                    content_type = "text/html; charset=utf-8"
                else:
                    found = site.pages.get(path)
                    if not found:
                        self.send_response(404)
                        self.send_header("Content-Type", "text/plain")
                        self.end_headers()
                        self.wfile.write(b"not found")
                        return
                    content_type, body = found
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        return Handler

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
