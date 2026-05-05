from __future__ import annotations

from pathlib import Path
import textwrap

from .config import REPORT_DIR
from .db import connect, now_iso


def save_report(user_id: int, session_id: int | None, markdown: str, title: str = "项目需求分析报告") -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        cur = db.execute(
            "INSERT INTO reports(user_id, session_id, title, markdown, created_at) VALUES(?, ?, ?, ?, ?)",
            (user_id, session_id, title, markdown, now_iso()),
        )
        report_id = cur.lastrowid
    pdf_path = REPORT_DIR / f"report_{report_id}.pdf"
    markdown_to_pdf(markdown, pdf_path)
    with connect() as db:
        db.execute("UPDATE reports SET pdf_path=? WHERE id=?", (str(pdf_path), report_id))
    return {"id": report_id, "title": title, "markdown": markdown, "pdf_path": str(pdf_path)}


def markdown_to_pdf(markdown: str, path: Path) -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(path), pagesize=A4)
        width, height = A4
        x, y = 48, height - 56
        for raw_line in markdown.splitlines():
            line = raw_line.replace("#", "").strip() if raw_line.startswith("#") else raw_line
            for part in textwrap.wrap(line, width=78) or [""]:
                c.drawString(x, y, part[:100])
                y -= 16
                if y < 48:
                    c.showPage()
                    y = height - 56
        c.save()
        return
    except Exception:
        pass
    write_minimal_pdf(markdown, path)


def write_minimal_pdf(text: str, path: Path) -> None:
    safe_lines = []
    for line in text.splitlines():
        ascii_line = line.encode("latin-1", errors="replace").decode("latin-1")
        safe_lines.extend(textwrap.wrap(ascii_line, 90) or [""])
    stream_lines = ["BT", "/F1 10 Tf", "48 800 Td", "14 TL"]
    for line in safe_lines[:240]:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_lines.append(f"({escaped}) Tj")
        stream_lines.append("T*")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref}\n%%EOF".encode())
    path.write_bytes(pdf)
