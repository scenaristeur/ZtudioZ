import re
import unicodedata


def slugify(text: str) -> str:
    """Normalize a name into a filesystem-safe slug.

    Handles French accented characters (é, è, ê, à, ù, ç, etc.)
    by decomposing Unicode and stripping combining marks.
    """
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


__all__ = ["slugify"]
