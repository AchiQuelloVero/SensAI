
def export_session_markdown(session: dict, output_path: str) -> None:
    """Export a conversation session to a Markdown file."""
    import os

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    lines = [
        f"# Sensai Conversation: {session.get('id', 'Unknown')}",
        "",
        f"- **Model:** {session.get('model', 'Unknown')}",
        f"- **Created:** {session.get('created_at', 'Unknown')}",
        f"- **Updated:** {session.get('updated_at', 'Unknown')}",
        "",
        "---",
        "",
    ]

    for message in session.get("messages", []):
        role = message.get("role", "unknown").capitalize()
        content = message.get("content", "")

        if role == "System":
            continue

        lines.extend([f"## {role}", ""])

        if content:
            lines.extend([content, ""])

        for tool_call in message.get("tool_calls", []):
            function = tool_call.get("function", {})
            name = function.get("name", "unknown")
            arguments = function.get("arguments", {})

            lines.extend([
                f"**Tool call:** `{name}`",
                "",
                "```json",
                __import__("json").dumps(
                    arguments, indent=2, ensure_ascii=False
                ),
                "```",
                "",
            ])

        if role == "Tool":
            tool_name = message.get("tool_name")
            if tool_name:
                lines.extend([f"**Tool:** `{tool_name}`", ""])

    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines).rstrip() + "\n")
