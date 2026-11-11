"""Writes tests/fixtures/resume_synthetic.pdf: a one-page resume of a made-up person.

Plain PDF written by hand (no libraries), so the fixture is reproducible and contains no real
person's data.   python tools/make_resume_fixture.py
"""

from pathlib import Path

LINES = [
    "Asha Verma",
    "asha.verma@example.com | +91 98765 43210 | https://github.com/asha-example",
    "Software engineer, 1 year. Python, Django, SQL, React.",
    "Built REST APIs with Django for an internship project in 2023.",
]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build() -> bytes:
    stream = "BT /F1 11 Tf 50 780 Td 14 TL\n" + "".join(
        f"({_escape(line)}) Tj T*\n" for line in LINES) + "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return out


if __name__ == "__main__":
    target = Path(__file__).parents[1] / "tests" / "fixtures" / "resume_synthetic.pdf"
    target.write_bytes(build())
    print(f"wrote {target}")
