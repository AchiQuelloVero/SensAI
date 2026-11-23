
import json
import os
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Preformatted,
)


def export_session_pdf(session: dict, output_path: str) -> None:
    """Export a conversation session to a PDF file."""
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    document = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Sensai Conversation {session.get('id', '')}",
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ConversationTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name="MessageRole",
        parent=styles["Heading2"],
        spaceBefore=10,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="MessageContent",
        parent=styles["BodyText"],
        leading=14,
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="ToolDetails",
        parent=styles["Code"],
        fontSize=8,
        leading=10,
        leftIndent=8,
        rightIndent=8,
        spaceAfter=8,
    ))

    story = [
        Paragraph("Sensai Conversation", styles["ConversationTitle"]),
        Paragraph(
            f"<b>Session:</b> {escape(str(session.get('id', 'Unknown')))}",
            styles["BodyText"],
        ),
        Paragraph(
            f"<b>Model:</b> {escape(str(session.get('model', 'Unknown')))}",
            styles["BodyText"],
        ),
        Paragraph(
            f"<b>Created:</b> {escape(str(session.get('created_at', 'Unknown')))}",
            styles["BodyText"],
        ),
        Paragraph(
            f"<b>Updated:</b> {escape(str(session.get('updated_at', 'Unknown')))}",
            styles["BodyText"],
        ),
        Spacer(1, 12),
    ]

    for message in session.get("messages", []):
        role = message.get("role", "unknown")
        if role == "system":
            continue

        story.append(Paragraph(escape(role.capitalize()), styles["MessageRole"]))

        content = message.get("content", "")
        if content:
            for line in str(content).splitlines() or [""]:
                story.append(
                    Paragraph(escape(line) or " ", styles["MessageContent"])
                )

        for tool_call in message.get("tool_calls", []):
            function = tool_call.get("function", {})
            name = function.get("name", "unknown")
            arguments = json.dumps(
                function.get("arguments", {}),
                indent=2,
                ensure_ascii=False,
            )
            details = f"Tool call: {name}\n{arguments}"
            story.append(Preformatted(details, styles["ToolDetails"]))

        if role == "tool" and message.get("tool_name"):
            story.append(Paragraph(
                f"<b>Tool:</b> {escape(str(message['tool_name']))}",
                styles["MessageContent"],
            ))

    document.build(story)
