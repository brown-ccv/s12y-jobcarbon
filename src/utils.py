from pathlib import Path


def output_text(content: str, path: Path | None) -> None:
    """Print to file or stdout"""
    if path is not None:
        path.write_text(content)
    else:
        print(content, end="")
