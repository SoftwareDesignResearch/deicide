# Deicide

A tool for automatically decomposing god classes into smaller, more cohesive components.

## Installation

1. Download the release archive for your platform from [Releases](https://github.com/SoftwareDesignResearch/deicide/releases)

   | Platform | File |
   |---|---|
   | macOS ARM (Apple Silicon) | `deicide-vX.X.X-aarch64-apple-darwin.tar.gz` |
   | macOS Intel | `deicide-vX.X.X-x86_64-apple-darwin.tar.gz` |
   | Windows | `deicide-vX.X.X-x86_64-pc-windows-msvc.zip` |
   | Linux | `deicide-vX.X.X-x86_64-unknown-linux-gnu.tar.gz` |

2. Extract the archive:

   **macOS/Linux:**
   ```bash
   tar -xzf deicide-vX.X.X-<platform>.tar.gz
   cd deicide-vX.X.X-<platform>
   ```

   **Windows:**
   Extract the zip file, then open a terminal in the extracted folder.

3. Install [uv](https://docs.astral.sh/uv/) (Python package manager):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

4. Verify installation:
   ```bash
   uv run deicide --help
   ```

**macOS note:** If blocked by Gatekeeper, run:
```bash
xattr -dr com.apple.quarantine neodepends/*
```

## Usage

### Option 1: Source code directly (recommended)

Run deicide directly on a source code repository. The `dependency-analyzer` binary from NeoDepends is bundled in the `neodepends/` directory.

```bash
uv run deicide \
  --source /path/to/repo \
  --language python \
  --output results.txt \
  --filename survey.py \
  --dv8-result
```

- `--source` — path to the source code repository
- `--language` — `python` or `java`
- `--output` — path for the output clustering file
- `--filename` — the file in the repository to decompose
- `--dv8-result` — also generate DV8-compatible `.dv8-clustering.json` and `.dv8-dependency.json` files
- `--neodepends` — (optional) path to a different `dependency-analyzer` binary, if not using the bundled one

The neodepends output (including the database) is saved to a `<output-name>_neodepends/` folder alongside the output file.

### Option 2: Pre-existing database

If you already have a `.db` file from NeoDepends, you can pass it directly:

```bash
uv run deicide \
  --input analysis.db \
  --output results.txt \
  --filename survey.py \
  --dv8-result
```

## Example

```bash
git clone https://github.com/apache/doris
uv run deicide \
  --source doris/ \
  --language java \
  --output doris_clustering.txt \
  --filename fe/fe-core/src/main/java/org/apache/doris/dictionary/DictionaryManager.java \
  --dv8-result
```

## Output

- `<name>.txt` — clustering assignments for each method/field
- `<name>.dv8-clustering.json` — DV8-compatible clustering (open in DV8 Explorer)
- `<name>.dv8-dependency.json` — DV8-compatible dependency matrix (open in DV8 Explorer)
