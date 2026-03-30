import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import click

from deicide.core import Entity
from deicide.db import DbDriver
from deicide.deicide import deicide
from deicide.dv8 import create_dv8_clustering, create_dv8_dependency
from deicide.semantic import KielaClarkSimilarity

logger = logging.getLogger(__name__)


def _find_dependency_analyzer() -> Path | None:
    """Look for the dependency-analyzer binary in common locations."""
    # Check bundled location (neodepends/ directory next to project root)
    bundled = Path(__file__).resolve().parent.parent.parent / "neodepends" / "dependency-analyzer"
    if bundled.exists():
        return bundled
    # Check PATH
    found = shutil.which("dependency-analyzer")
    if found:
        return Path(found)
    return None


def _run_neodepends(
    neodepends: Path, source: Path, language: str, output_dir: Path
) -> Path:
    """Run the dependency-analyzer and return the path to the enhanced .db file."""
    logger.info(f"Running neodepends on {source} ({language})...")
    cmd = [
        str(neodepends),
        "--input", str(source),
        "--output", str(output_dir),
        "--language", language,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"neodepends failed:\n{result.stderr}")
        quit(-1)
    logger.info("neodepends completed successfully.")

    # Find the enhanced db (post-processed)
    db_path = output_dir / "data" / "dependencies.stackgraphs_ast.db"
    if not db_path.exists():
        # Fall back to any .db file in data/
        db_files = list((output_dir / "data").glob("*.db"))
        if not db_files:
            logger.error("No .db file found in neodepends output.")
            quit(-1)
        db_path = db_files[0]

    return db_path


@click.command()
@click.option(
    "--input",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to SQLite DB from neodepends.",
)
@click.option(
    "--source",
    required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to source code repository (runs neodepends automatically).",
)
@click.option(
    "--language",
    required=False,
    type=click.Choice(["python", "java"]),
    help="Language of the source code (required with --source).",
)
@click.option(
    "--neodepends",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the dependency-analyzer binary (auto-detected if in PATH).",
)
@click.option(
    "--output",
    required=True,
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    help="Path to output text file. Must not exist.",
)
@click.option("--filename", required=True, type=str, help="Filename in the database.")
@click.option(
    "--commit-hash",
    required=False,
    type=str,
    help="Commit hash (required if DB has multiple versions).",
)
@click.option(
    "--dv8-result",
    is_flag=True,
    default=False,
    help="Generate DV8 clustering output (.dv8-clustering.json) and DV8 dependency \
        output",
)
def main(
    input: Path | None,
    source: Path | None,
    language: str | None,
    neodepends: Path | None,
    output: Path,
    filename: str,
    commit_hash: str | None,
    dv8_result: bool,
) -> None:
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Determine input mode
    if source and input:
        logger.error("Cannot specify both --input and --source.")
        quit(-1)
    if not source and not input:
        logger.error("Must specify either --input (db file) or --source (source code directory).")
        quit(-1)

    if source:
        if not language:
            logger.error("--language is required when using --source.")
            quit(-1)
        # Find neodepends binary
        nd_path = neodepends or _find_dependency_analyzer()
        if not nd_path:
            logger.error(
                "Could not find dependency-analyzer. "
                "Provide --neodepends or add it to PATH."
            )
            quit(-1)
        # Run neodepends into a temp directory alongside the output
        nd_output = output.parent / f"{output.stem}_neodepends"
        nd_output.mkdir(parents=True, exist_ok=True)
        input = _run_neodepends(nd_path, source, language, nd_output)

    # Open database
    db_driver = DbDriver(input)

    # Ensure commit_hash is set
    commit_hashes = db_driver.load_commit_hashes()
    if len(commit_hashes) == 0:
        logger.error("Database contains no commit hashes.")
        quit(-1)
    if commit_hash and commit_hash not in commit_hashes:
        logger.error(
            "Given commit hash ({commit_hash}) does not exist in the database."
        )
        quit(-1)
    if not commit_hash and len(commit_hashes) > 1:
        logger.error(
            f"Database contains multiple commit hashes ({','.join(commit_hashes)}). \
                Please specify using --commit-hash."
        )
        quit(-1)
    if not commit_hash:
        commit_hash = commit_hashes[0]
    db_driver.set_commit_hash(commit_hash)

    # Ensure filename exists
    filenames = db_driver.load_filenames()
    if filename not in filenames:
        logger.error(f"Database does not contain filename ({filename}).")
    parent_id = filenames[filename]

    # Load children
    children = db_driver.load_children(parent_id)
    if len(children) == 1:
        # If there is only one top level item (e.g. a Java class), skip to its children.
        parent_id = children[0].id
        children = db_driver.load_children(parent_id)
    if len(children) == 0:
        logger.error("File contains no entities")
        quit(-1)

    # Load clients
    clients = db_driver.load_clients(parent_id)

    # Load deps
    internal_deps = db_driver.load_internal_deps(parent_id)
    client_deps = db_driver.load_client_deps(parent_id)

    # Create semantic similarity
    semantic = KielaClarkSimilarity()
    semantic.fit({e.id: e.name for e in children})

    # Run algorithm
    clustering = deicide(children, clients, internal_deps + client_deps, semantic)

    # Create hex_id to entity mapping (file entities + clients)
    id_to_entity = {entity.id: entity for entity in children}

    # Add clients to the mapping with modified names
    for client in clients:
        modified_client = Entity(
            id=client.id,
            name=f"(Client) {client.name}",
            parent_id=client.parent_id,
            kind=client.kind,
        )
        id_to_entity[modified_client.id] = modified_client

    # Write output
    with open(output, "w") as f:
        for id, cluster in clustering:
            name = id_to_entity[id].name
            f.write(f"{name} : {cluster}\n")

    output_name = output.stem

    # Generate optional output based on flags
    if dv8_result:
        dv8_clustering = create_dv8_clustering(clustering, id_to_entity, output_name)
        dv8_output = output.with_suffix(".dv8-clustering.json")
        with open(dv8_output, "w") as f:
            json.dump(dv8_clustering.to_dict(), f, indent=2)
        dsm_dependencies = create_dv8_dependency(
            id_to_entity, internal_deps, client_deps, output_name
        )
        dsm_output = output.with_suffix(".dv8-dependency.json")
        with open(dsm_output, "w") as f:
            json.dump(dsm_dependencies, f, indent=2)


if __name__ == "__main__":
    main()
