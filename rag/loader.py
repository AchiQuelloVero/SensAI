import logging
import os
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from rag.errors import RagError

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf")

# pypdf logs a warning for every malformed object it skips, which floods the terminal
logging.getLogger("pypdf").setLevel(logging.ERROR)


def find_documents(docs_dir) -> list[Path]:
    root = Path(docs_dir)
    if not root.is_dir():
        raise RagError(
            f"documents folder not found: {docs_dir} "
            "(create it or set rag_docs_dir in config.json)"
        )

    paths = []
    for folder, subfolders, names in os.walk(root):
        # skip hidden folders like .git, editing the list in place stops os.walk from entering them
        subfolders[:] = [name for name in subfolders if not name.startswith(".")]
        for name in names:
            if name.startswith(".") or not name.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            paths.append(Path(folder) / name)

    return sorted(paths)


def source_name(path: Path, docs_dir) -> str:
    # the name shown in citations: the path inside the documents folder
    return path.relative_to(docs_dir).as_posix()


def read_pdf(path: Path) -> str:
    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except FileNotDecryptedError:
        raise RagError(f"cannot read {path}: the PDF is password-protected")
    except OSError:
        raise
    except Exception as e:  # pypdf raises many exception types on malformed files
        raise RagError(f"cannot read {path}: not a valid PDF ({e})")

    return "\n\n".join(pages)


def read_document(path: Path) -> str:
    try:
        if path.suffix.lower() == ".pdf":
            text = read_pdf(path)
        else:
            text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RagError(f"cannot read {path}: not UTF-8 text")
    except OSError as e:
        raise RagError(f"cannot read {path}: {e.strerror or e}")

    text = text.strip()
    if not text:
        # also what a scanned PDF gives: images only, no text layer
        raise RagError(f"{path} has no text to index")

    return text
