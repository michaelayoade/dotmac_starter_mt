"""Small valid uncompressed PDF fixtures, authored for hosted contract tests.

This is a test document builder, not a PDF engine or production rendering path.
Object offsets and the xref are computed from the actual serialized byte lengths.
"""

import zlib

from dotmac_document_rendering import LabelledValue, StaticText, Table


def display_text(projection):
    values = []
    for section in projection.sections:
        for block in section.blocks:
            if isinstance(block, StaticText):
                values.append(block.text)
            elif isinstance(block, LabelledValue):
                values.extend((block.label_text, block.value.text))
            elif isinstance(block, Table):
                values.extend(column.label_text for column in block.columns)
                values.extend(value.text for row in block.rows for value in row)
    return values


def text_pdf(texts, *, action=None, compressed=False, padding_bytes=0):
    """A real PDF with ordered visible text, standard font, pages and xref.

    Optional dangerous features are genuine PDF dictionaries, rather than byte
    strings inside visible text, so hosted validation tests exercise structure.
    """
    normalized = [" ".join(text.split()) for text in texts if text.strip()]
    chunks = [normalized[i : i + 50] for i in range(0, len(normalized), 50)] or [[]]
    page_ids = [4 + 2 * i for i in range(len(chunks))]
    catalog = b"<< /Type /Catalog /Pages 2 0 R"
    if action == "javascript":
        catalog += b" /OpenAction << /S /JavaScript /JS (app.alert\\(1\\)) >>"
    catalog += b" >>"
    objects = [
        catalog,
        b"<< /Type /Pages /Count "
        + str(len(chunks)).encode()
        + b" /Kids ["
        + b" ".join(f"{i} 0 R".encode() for i in page_ids)
        + b"] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, lines in zip(page_ids, chunks, strict=True):
        page = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(page_id + 1).encode()
            + b" 0 R"
        )
        if action == "uri":
            page += (
                b" /Annots [<< /Type /Annot /Subtype /Link /Rect [0 0 100 100] "
                b"/A << /S /URI /URI (https://unfrozen.example.test/) >> >>]"
            )
        page += b" >>"
        commands = [b"BT /F1 9 Tf 40 760 Td 13 TL"]
        for index, line in enumerate(lines):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if index:
                commands.append(b"T*")
            commands.append(b"(" + escaped.encode("latin-1") + b") Tj")
        commands.append(b"ET")
        padding = padding_bytes if page_id == page_ids[0] else 0
        stream = b"\n".join(commands) + b"\n" + b" " * padding
        filters = b""
        if compressed:
            stream = zlib.compress(stream)
            filters = b" /Filter /FlateDecode"
        objects.extend(
            (
                page,
                b"<< /Length "
                + str(len(stream)).encode()
                + filters
                + b" >>\nstream\n"
                + stream
                + b"endstream",
            )
        )
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(offsets)}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(payload)
