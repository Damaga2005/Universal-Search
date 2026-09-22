"""Plain-text extraction for text formats and source code."""

from pathlib import Path

from universal_search.domain.extraction import ExtractionResult


TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
    ".hpp", ".cs", ".go", ".rs", ".sql", ".yaml", ".yml", ".toml",
    ".ini", ".log",
}

# Upper bound for extracted text, in characters, so a single file can never
# cause uncontrolled memory growth in the indexer or the search engine.
MAX_CONTENT_CHARS = 2_000_000


def normalize_text(text: str) -> str:
    """Remove characters that would corrupt storage or display."""
    return text.replace("\x00", "").replace("\ufeff", "")


def is_text_extension(extension: str) -> bool:
    return extension.lower() in TEXT_EXTENSIONS


def read_text(path: Path) -> ExtractionResult:
    """Read a text file with a bounded size, or report why it failed."""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read(MAX_CONTENT_CHARS)
    except OSError as exc:
        return ExtractionResult(error=f"{type(exc).__name__}: {exc}")
    return ExtractionResult(text=normalize_text(text))
