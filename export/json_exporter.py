
import json
import os


def export_session_json(session: dict, output_path: str) -> None:
    #Export JSON file.
    directory = os.path.dirname(output_path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(session, file, indent=2, ensure_ascii=False)
        file.write("\n")
