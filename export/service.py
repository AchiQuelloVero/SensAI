
from storage.session_store import load_session
from export.json_exporter import export_session_json
from export.markdown_exporter import export_session_markdown
from export.pdf_exporter import export_session_pdf


def export_saved_session(
    session_id: str,
    sessions_dir: str = "sessions",
    output_format: str = "json",
    output_path: str | None = None,
) -> str:
    session, _ = load_session(sessions_dir, session_id)

    extensions = {
        "json": "json",
        "markdown": "md",
        "pdf": "pdf",
    }

    exporters = {
        "json": export_session_json,
        "markdown": export_session_markdown,
        "pdf": export_session_pdf,
    }

    if output_format not in exporters:
        raise ValueError(f"Unsupported export format: {output_format}")

    if output_path is None:
        extension = extensions[output_format]
        output_path = f"session_{session_id}.{extension}"

    exporters[output_format](session, output_path)

    return output_path
