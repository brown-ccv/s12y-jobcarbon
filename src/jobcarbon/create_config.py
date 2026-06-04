import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from .config import Config
from .utils import output_text

app = typer.Typer()

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


@app.command()
def main(
    output: Annotated[
        Path | None, typer.Option(help="output file (default: stdout)")
    ] = None,
) -> None:
    """Generate jobcarbon.toml from sinfo output.

    Usage: sinfo -h -o "%N %G" | jobcarbon-create-config
    """
    if sys.stdin.isatty():
        logger.error(
            'No piped data. Use: sinfo -h -o "%%N %%G" | jobcarbon-create-config'
        )
        raise typer.Exit(1)

    content = Config.generate(sys.stdin.readlines())
    output_text(content, output)


if __name__ == "__main__":
    app()
