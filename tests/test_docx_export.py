import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from tailoring.docx_export import NAME_ACCENT_COLOR, NAME_SIZE, BODY_FONT, BODY_SIZE, text_to_docx_bytes

SAMPLE_RESUME = (
    "Jane Doe\n"
    "jane@example.com | (555) 123-4567 | linkedin.com/in/janedoe\n"
    "\n"
    "PROFESSIONAL SUMMARY\n"
    "Some summary text here.\n"
    "\n"
    "PROFESSIONAL EXPERIENCE\n"
    "Acme Corp  01/2020 - Present\n"
    "- Did a thing.\n"
)


def _load(data: bytes) -> Document:
    return Document(io.BytesIO(data))


def test_name_is_center_aligned_like_the_base_template():
    # Regression for a real bug (2026-08-04): the name paragraph was styled
    # bold/large/colored but never actually centered, even though the
    # contact-info line right under it always was - so a generated resume's
    # name sat flush-left while everything else matched Zahir's real resume
    # template it was ported from.
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    name_para = doc.paragraphs[0]
    assert name_para.text == "Jane Doe"
    assert name_para.alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_name_run_is_bold_large_and_accent_colored():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    run = doc.paragraphs[0].runs[0]
    assert run.bold is True
    assert run.font.size == NAME_SIZE
    assert run.font.color.rgb == NAME_ACCENT_COLOR


def test_contact_line_is_center_aligned():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    contact_para = doc.paragraphs[1]
    assert "jane@example.com" in contact_para.text
    assert contact_para.alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_name_size_scales_with_body_size_pt_and_stays_centered():
    # USAJOBS resumes shrink body text to fit the 2-page hard cap
    # (tailoring/dossier.py's body_size_pt override) - the name should
    # shrink proportionally and stay centered, not revert to a default.
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME, body_size_pt=9.0))
    name_para = doc.paragraphs[0]
    run = name_para.runs[0]
    assert run.font.size.pt == round(9.0 * (NAME_SIZE.pt / 10.5))
    assert name_para.alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_base_font_and_size_applied_to_normal_style():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    normal = doc.styles["Normal"]
    assert normal.font.name == BODY_FONT
    assert normal.font.size == BODY_SIZE


def test_section_header_is_bold_and_accent_colored_not_centered():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    header_para = next(p for p in doc.paragraphs if p.text == "PROFESSIONAL SUMMARY")
    run = header_para.runs[0]
    assert run.bold is True
    assert run.font.color.rgb == NAME_ACCENT_COLOR
    assert header_para.alignment != WD_ALIGN_PARAGRAPH.CENTER


def test_date_range_line_is_bold_not_centered():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    role_para = next(p for p in doc.paragraphs if "Acme Corp" in p.text)
    assert role_para.runs[0].bold is True
    assert role_para.alignment != WD_ALIGN_PARAGRAPH.CENTER


def test_bullet_line_uses_list_bullet_style():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME))
    bullet_para = next(p for p in doc.paragraphs if p.text == "Did a thing.")
    assert bullet_para.style.name == "List Bullet"


def test_author_sets_core_properties_instead_of_python_docx_default():
    doc = _load(text_to_docx_bytes(SAMPLE_RESUME, author="Jane Doe"))
    assert doc.core_properties.author == "Jane Doe"
    assert doc.core_properties.last_modified_by == "Jane Doe"
