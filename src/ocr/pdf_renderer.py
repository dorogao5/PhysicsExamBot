from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium


def render_pdf_to_pngs(pdf_path: Path, output_dir: Path, scale: float = 2.0) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))

    page_paths: list[Path] = []
    for index in range(len(document)):
        page = document[index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        output_path = output_dir / f"page_{index + 1:04d}.png"
        image.save(output_path, format="PNG")
        page_paths.append(output_path)

    return page_paths
