import os
import time
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


class ExportService:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.pdf_font_name = self._register_fonts()

    def _register_fonts(self) -> str:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            return "STSong-Light"
        except Exception:
            pass
        possible_fonts = [
            r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
        for font_path in possible_fonts:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont("ChineseFont", font_path))
                    return "ChineseFont"
                except Exception:
                    continue
        return "Helvetica"

    def export_docx(self, title: str, content: str) -> str:
        path = os.path.join(self.output_dir, f"{self._safe_name(title)}_{int(time.time())}.docx")
        normalized_title = self._normalize_text(title)
        normalized_content = self._normalize_text(content)
        document = Document()
        heading = document.add_heading(normalized_title, level=1)
        for run in heading.runs:
            run.font.name = "Microsoft YaHei"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            run.font.size = Pt(16)
        for line in normalized_content.splitlines():
            paragraph = document.add_paragraph(line)
            for run in paragraph.runs:
                run.font.name = "Microsoft YaHei"
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
                run.font.size = Pt(11)
        document.save(path)
        return path

    def export_pdf(self, title: str, content: str) -> str:
        path = os.path.join(self.output_dir, f"{self._safe_name(title)}_{int(time.time())}.pdf")
        normalized_title = self._normalize_text(title)
        normalized_content = self._normalize_text(content)
        pdf = canvas.Canvas(path, pagesize=A4)
        font_name = self.pdf_font_name
        font_size = 11
        line_height = 16
        page_width, page_height = A4
        left_margin = 40
        right_margin = 40
        top_margin = 48
        bottom_margin = 48
        usable_width = page_width - left_margin - right_margin
        try:
            pdf.setFont(font_name, font_size)
        except Exception:
            font_name = "Helvetica"
            pdf.setFont(font_name, font_size)
        text_object = pdf.beginText(left_margin, page_height - top_margin)
        text_object.setLeading(line_height)
        text_object.setFont(font_name, font_size)
        text_object.textLine(normalized_title)
        text_object.textLine("")
        for raw_line in normalized_content.splitlines():
            wrapped_lines = self._wrap_pdf_line(raw_line, font_name, font_size, usable_width)
            if not wrapped_lines:
                wrapped_lines = [""]
            for line in wrapped_lines:
                text_object.textLine(line)
                if text_object.getY() < bottom_margin:
                    pdf.drawText(text_object)
                    pdf.showPage()
                    pdf.setFont(font_name, font_size)
                    text_object = pdf.beginText(left_margin, page_height - top_margin)
                    text_object.setLeading(line_height)
                    text_object.setFont(font_name, font_size)
        if text_object.getY() < bottom_margin:
                pdf.drawText(text_object)
                pdf.showPage()
                pdf.setFont(font_name, font_size)
                text_object = pdf.beginText(left_margin, page_height - top_margin)
                text_object.setLeading(line_height)
                text_object.setFont(font_name, font_size)
        pdf.drawText(text_object)
        pdf.save()
        return path

    def _normalize_text(self, value: str) -> str:
        return value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")

    def _wrap_pdf_line(self, text: str, font_name: str, font_size: int, max_width: float) -> list[str]:
        if not text:
            return [""]
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = f"{current}{char}"
            width = pdfmetrics.stringWidth(candidate, font_name, font_size)
            if width <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = char
        if current:
            lines.append(current)
        return lines

    def _safe_name(self, value: str) -> str:
        cleaned = value.replace("/", "_").replace("\\", "_").replace(":", "_").replace("*", "_").replace("?", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")
        return cleaned[:30] or "script"
