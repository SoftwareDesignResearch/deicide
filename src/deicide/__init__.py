import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

import click

from deicide.core import Entity
from deicide.db import DbDriver
from deicide.deicide import deicide
from deicide.dv8 import create_dv8_clustering, create_dv8_dependency
from deicide.semantic import KielaClarkSimilarity

logger = logging.getLogger(__name__)


GROUP_COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red", "white", "bright_cyan", "bright_green", "bright_yellow"]


def _print_header():
    click.secho("=" * 60, fg="bright_white")
    click.secho("Deicide", fg="bright_white", bold=True)
    click.secho("=" * 60, fg="bright_white")
    click.echo()


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

    # Run with a spinner since this can take a while on large repos
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    stop_spinner = threading.Event()
    start = time.time()

    def _spinner():
        i = 0
        while not stop_spinner.is_set():
            elapsed = time.time() - start
            sys.stderr.write(f"\r      {spinner_chars[i % len(spinner_chars)]} Analyzing dependencies... ({elapsed:.0f}s)")
            sys.stderr.flush()
            i += 1
            stop_spinner.wait(0.1)
        elapsed = time.time() - start
        sys.stderr.write(f"\r      Dependency analysis completed ({elapsed:.1f}s)     \n")
        sys.stderr.flush()

    t = threading.Thread(target=_spinner, daemon=True)
    t.start()
    result = subprocess.run(cmd, capture_output=True, text=True)
    stop_spinner.set()
    t.join()

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
    # Set up logging (suppress log lines, we'll use click.echo for output)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _print_header()
    start_time = time.time()

    # Determine input mode
    if source and input:
        click.secho("Error: Cannot specify both --input and --source.", fg="red")
        quit(-1)
    if not source and not input:
        click.secho("Error: Must specify either --input (db file) or --source (source code directory).", fg="red")
        quit(-1)

    if source:
        if not language:
            click.secho("Error: --language is required when using --source.", fg="red")
            quit(-1)
        # Find neodepends binary
        nd_path = neodepends or _find_dependency_analyzer()
        if not nd_path:
            click.secho(
                "Error: Could not find dependency-analyzer. "
                "Provide --neodepends or add it to PATH.",
                fg="red",
            )
            quit(-1)
        # Run neodepends into a temp directory alongside the output
        nd_output = output.parent / f"{output.stem}_neodepends"
        nd_output.mkdir(parents=True, exist_ok=True)
        click.secho("[1/3] Running dependency analysis...", fg="bright_white", bold=True)
        click.echo(f"      Source:   {source}")
        click.echo(f"      Language: {language}")
        input = _run_neodepends(nd_path, source, language, nd_output)
        click.secho("      Done.", fg="green")
        click.echo()
    else:
        click.secho("[1/3] Loading database...", fg="bright_white", bold=True)
        click.echo(f"      Database: {input}")
        click.echo()

    # Open database
    db_driver = DbDriver(input)

    # Ensure commit_hash is set
    commit_hashes = db_driver.load_commit_hashes()
    if len(commit_hashes) == 0:
        click.secho("Error: Database contains no commit hashes.", fg="red")
        quit(-1)
    if commit_hash and commit_hash not in commit_hashes:
        click.secho(
            f"Error: Given commit hash ({commit_hash}) does not exist in the database.",
            fg="red",
        )
        quit(-1)
    if not commit_hash and len(commit_hashes) > 1:
        click.secho(
            f"Error: Database contains multiple commit hashes ({','.join(commit_hashes)}). "
            "Please specify using --commit-hash.",
            fg="red",
        )
        quit(-1)
    if not commit_hash:
        commit_hash = commit_hashes[0]
    db_driver.set_commit_hash(commit_hash)

    # Ensure filename exists
    filenames = db_driver.load_filenames()
    if filename not in filenames:
        click.secho(f"Error: Database does not contain filename ({filename}).", fg="red")
        quit(-1)
    parent_id = filenames[filename]

    # Load children
    children = db_driver.load_children(parent_id)
    if len(children) == 1:
        # If there is only one top level item (e.g. a Java class), skip to its children.
        parent_id = children[0].id
        children = db_driver.load_children(parent_id)
    if len(children) == 0:
        click.secho("Error: File contains no entities", fg="red")
        quit(-1)

    # Load clients
    clients = db_driver.load_clients(parent_id)

    # Load deps
    internal_deps = db_driver.load_internal_deps(parent_id)
    client_deps = db_driver.load_client_deps(parent_id)

    # Summarize what was loaded
    kind_counts = defaultdict(int)
    for e in children:
        kind_counts[e.kind] += 1

    dep_type_counts = defaultdict(int)
    for d in internal_deps + client_deps:
        dep_type_counts[d.kind] += 1

    click.secho(f"[2/3] Analyzing: {filename}", fg="bright_white", bold=True)
    click.echo(f"      Entities:     {len(children)} ({', '.join(f'{v} {k.lower()}s' for k, v in sorted(kind_counts.items()))})")
    click.echo(f"      Clients:      {len(clients)} ({', '.join(c.name for c in clients) if clients else 'none'})")
    click.echo(f"      Dependencies: {len(internal_deps)} internal, {len(client_deps)} client")
    if dep_type_counts:
        click.echo(f"      Dep types:    {', '.join(f'{k}: {v}' for k, v in sorted(dep_type_counts.items()))}")
    click.echo()

    # Create semantic similarity
    semantic = KielaClarkSimilarity()
    semantic.fit({e.id: e.name for e in children})

    # Run algorithm
    click.secho("[3/3] Running decomposition...", fg="bright_white", bold=True)
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

    # Build cluster groups for display using top 2 levels of the path
    cluster_groups: dict[tuple, list[str]] = defaultdict(list)
    client_list: list[str] = []
    for entity_id, cluster_path in clustering:
        name = id_to_entity[entity_id].name
        if name.startswith("(Client)"):
            client_list.append(name)
        else:
            # Use up to first 2 levels for grouping
            key = tuple(cluster_path[:2]) if len(cluster_path) >= 2 else tuple(cluster_path)
            cluster_groups[key].append(name)

    click.echo()
    click.secho("-" * 60, fg="bright_white")
    click.secho("Decomposition Results", fg="bright_white", bold=True)
    click.secho("-" * 60, fg="bright_white")
    for i, (key, members) in enumerate(sorted(cluster_groups.items())):
        color = GROUP_COLORS[i % len(GROUP_COLORS)]
        click.secho(f"\n  Group {i + 1} ({len(members)} entities):", fg=color, bold=True)
        for m in members:
            click.secho(f"    - {m}", fg=color)
    if client_list:
        click.secho(f"\n  Clients ({len(client_list)} external files):", fg="bright_black", bold=True)
        for c in client_list:
            click.secho(f"    - {c}", fg="bright_black")

    # Write output
    with open(output, "w") as f:
        for id, cluster in clustering:
            name = id_to_entity[id].name
            f.write(f"{name} : {cluster}\n")

    output_name = output.stem

    # Generate optional output based on flags
    click.echo()
    click.secho("-" * 60, fg="bright_white")
    click.secho("Output Files", fg="bright_white", bold=True)
    click.secho("-" * 60, fg="bright_white")
    click.echo(f"  Clustering:     {output}")

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
        click.echo(f"  DV8 Clustering: {dv8_output}")
        click.echo(f"  DV8 Dependency: {dsm_output}")

    elapsed = time.time() - start_time
    click.echo()
    click.secho(f"Done in {elapsed:.1f}s", fg="green", bold=True)
    click.secho("=" * 60, fg="bright_white")


if __name__ == "__main__":
    main()
