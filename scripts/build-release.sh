#!/usr/bin/env bash
set -euo pipefail

# Deicide Release Builder
# Downloads neodepends binaries and packages deicide for each platform

DEICIDE_VERSION="${1:?Usage: build-release.sh <deicide-version> [neodepends-version]}"
NEODEPENDS_VERSION="${2:-v0.3.3}"
NEODEPENDS_REPO="SoftwareDesignResearch/neodepends"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/dist"

PLATFORMS=(
    "aarch64-apple-darwin"
    "x86_64-apple-darwin"
    "x86_64-pc-windows-msvc"
    "x86_64-unknown-linux-gnu"
)

echo "============================================================"
echo "Deicide Release Builder"
echo "============================================================"
echo "  Deicide version:    $DEICIDE_VERSION"
echo "  NeoDepends version: $NEODEPENDS_VERSION"
echo "  Platforms:           ${#PLATFORMS[@]}"
echo ""

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/downloads"

for PLATFORM in "${PLATFORMS[@]}"; do
    echo "------------------------------------------------------------"
    echo "Building for: $PLATFORM"
    echo "------------------------------------------------------------"

    RELEASE_NAME="deicide-${DEICIDE_VERSION}-${PLATFORM}"
    RELEASE_DIR="$BUILD_DIR/$RELEASE_NAME"
    rm -rf "$RELEASE_DIR"
    mkdir -p "$RELEASE_DIR"

    # Copy deicide source
    cp -r "$PROJECT_DIR/src" "$RELEASE_DIR/"
    cp "$PROJECT_DIR/pyproject.toml" "$RELEASE_DIR/"
    cp "$PROJECT_DIR/README.md" "$RELEASE_DIR/"
    cp "$PROJECT_DIR/uv.lock" "$RELEASE_DIR/" 2>/dev/null || true
    [ -f "$PROJECT_DIR/LICENSE" ] && cp "$PROJECT_DIR/LICENSE" "$RELEASE_DIR/"

    # Download neodepends for this platform
    if [[ "$PLATFORM" == *"windows"* ]]; then
        ND_EXT="zip"
    else
        ND_EXT="tar.gz"
    fi
    ND_FILENAME="neodepends-${NEODEPENDS_VERSION}-${PLATFORM}.${ND_EXT}"
    ND_URL="https://github.com/${NEODEPENDS_REPO}/releases/download/${NEODEPENDS_VERSION}/${ND_FILENAME}"
    ND_DOWNLOAD="$BUILD_DIR/downloads/$ND_FILENAME"

    if [ ! -f "$ND_DOWNLOAD" ]; then
        echo "  Downloading $ND_FILENAME..."
        curl -sL -o "$ND_DOWNLOAD" "$ND_URL"
    else
        echo "  Using cached $ND_FILENAME"
    fi

    # Extract neodepends into release
    echo "  Extracting neodepends..."
    ND_EXTRACT_DIR="$BUILD_DIR/downloads/neodepends-${PLATFORM}"
    rm -rf "$ND_EXTRACT_DIR"
    mkdir -p "$ND_EXTRACT_DIR"

    if [[ "$ND_EXT" == "zip" ]]; then
        unzip -q "$ND_DOWNLOAD" -d "$ND_EXTRACT_DIR"
    else
        tar -xzf "$ND_DOWNLOAD" -C "$ND_EXTRACT_DIR"
    fi

    # Find the extracted directory (might be nested)
    ND_INNER=$(find "$ND_EXTRACT_DIR" -maxdepth 1 -type d | tail -1)
    if [ -d "$ND_INNER/bin" ]; then
        mkdir -p "$RELEASE_DIR/neodepends"
        cp -r "$ND_INNER/bin" "$RELEASE_DIR/neodepends/"
        cp "$ND_INNER/dependency-analyzer"* "$RELEASE_DIR/neodepends/" 2>/dev/null || true
    fi

    # Remove pycache
    find "$RELEASE_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

    # Package
    echo "  Packaging..."
    cd "$BUILD_DIR"
    if [[ "$PLATFORM" == *"windows"* ]]; then
        zip -qr "${RELEASE_NAME}.zip" "$RELEASE_NAME/"
        echo "  Created: ${RELEASE_NAME}.zip"
    else
        tar -czf "${RELEASE_NAME}.tar.gz" "$RELEASE_NAME/"
        echo "  Created: ${RELEASE_NAME}.tar.gz"
    fi

    rm -rf "$RELEASE_DIR"
    echo ""
done

echo "============================================================"
echo "Release archives:"
echo "============================================================"
ls -lh "$BUILD_DIR"/*.{tar.gz,zip} 2>/dev/null
echo ""
echo "To create a GitHub release:"
echo "  gh release create $DEICIDE_VERSION $BUILD_DIR/*.tar.gz $BUILD_DIR/*.zip \\"
echo "    --title \"$DEICIDE_VERSION\" --notes-file RELEASE_NOTES.md"
echo "============================================================"
