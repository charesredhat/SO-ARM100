#!/bin/bash
# Setup script to install the Ralph agent into a LeRobot project
# Usage: ./setup-ralph.sh /path/to/your/lerobot-project
# Ralph is looked for in projects2/Ralph-agent; override with RALPH_DIR=/path/to/ralph

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_DIR="${RALPH_DIR:-$(cd "$SCRIPT_DIR/../../Ralph-agent" 2>/dev/null && pwd)}"
if [ -z "$RALPH_DIR" ] || [ ! -f "$RALPH_DIR/ralph.sh" ]; then
    echo -e "${RED}Error: Ralph not found. Set RALPH_DIR to the folder containing ralph.sh${NC}"
    exit 1
fi

echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Ralph Agent Setup for LeRobot Servo Debugging          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if target directory is provided
if [ -z "$1" ]; then
    echo -e "${YELLOW}Usage: $0 /path/to/your/lerobot-project${NC}"
    echo ""
    echo "Example:"
    echo "  $0 ~/projects/my-lerobot-project"
    echo ""
    exit 1
fi

TARGET_DIR="$1"

# Verify target directory exists
if [ ! -d "$TARGET_DIR" ]; then
    echo -e "${RED}Error: Directory does not exist: $TARGET_DIR${NC}"
    exit 1
fi

# Check if it's a git repository
if [ ! -d "$TARGET_DIR/.git" ]; then
    echo -e "${YELLOW}Warning: Target directory is not a git repository.${NC}"
    echo "Ralph requires git for tracking changes."
    read -p "Initialize git repository? (y/n): " init_git
    if [ "$init_git" = "y" ] || [ "$init_git" = "Y" ]; then
        cd "$TARGET_DIR"
        git init
        echo -e "${GREEN}Git repository initialized.${NC}"
    else
        echo -e "${RED}Aborting. Ralph requires a git repository.${NC}"
        exit 1
    fi
fi

echo ""
echo "Target directory: $TARGET_DIR"
echo "Ralph source: $RALPH_DIR"
echo "Debugger source: $SCRIPT_DIR"
echo ""

# Copy Ralph core files
echo -e "${YELLOW}Copying Ralph core files...${NC}"

cp "$RALPH_DIR/ralph.sh" "$TARGET_DIR/"
echo "  ✓ ralph.sh"

cp "$RALPH_DIR/CLAUDE.md" "$TARGET_DIR/"
echo "  ✓ CLAUDE.md"

cp "$RALPH_DIR/prompt.md" "$TARGET_DIR/"
echo "  ✓ prompt.md"

# Copy debugger project files
echo ""
echo -e "${YELLOW}Copying LeRobot debugger files...${NC}"

cp "$SCRIPT_DIR/prd.json" "$TARGET_DIR/"
echo "  ✓ prd.json"

cp "$SCRIPT_DIR/RESEARCH.md" "$TARGET_DIR/"
echo "  ✓ RESEARCH.md"

# Create progress.txt
echo ""
echo -e "${YELLOW}Creating progress tracking file...${NC}"

cat > "$TARGET_DIR/progress.txt" << EOF
# Ralph Progress Log - LeRobot Servo Debugger
Started: $(date)
Project: $(basename "$TARGET_DIR")

## Codebase Patterns
(Patterns will be added as Ralph discovers them)

---
EOF
echo "  ✓ progress.txt"

# Create src directory if it doesn't exist
mkdir -p "$TARGET_DIR/src"
echo "  ✓ src/ directory"

# Create diagnostics directory
mkdir -p "$TARGET_DIR/diagnostics"
echo "  ✓ diagnostics/ directory"

# Make ralph.sh executable
chmod +x "$TARGET_DIR/ralph.sh"

# Create a .gitignore entry for Ralph temp files if not exists
if ! grep -q ".last-branch" "$TARGET_DIR/.gitignore" 2>/dev/null; then
    echo "" >> "$TARGET_DIR/.gitignore"
    echo "# Ralph agent files" >> "$TARGET_DIR/.gitignore"
    echo ".last-branch" >> "$TARGET_DIR/.gitignore"
    echo "  ✓ Updated .gitignore"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete!                         ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Files installed:${NC}"
echo "  $TARGET_DIR/"
echo "  ├── ralph.sh          # Main Ralph loop script"
echo "  ├── CLAUDE.md         # Instructions for Claude Code"
echo "  ├── prompt.md         # Instructions for Amp"
echo "  ├── prd.json          # 7 user stories for servo debugging"
echo "  ├── RESEARCH.md       # LeRobot research documentation"
echo "  ├── progress.txt      # Progress tracking"
echo "  ├── src/              # Source code directory"
echo "  └── diagnostics/      # Output reports directory"
echo ""
echo -e "${YELLOW}To run Ralph:${NC}"
echo ""
echo "  cd $TARGET_DIR"
echo ""
echo "  # With Claude Code (recommended):"
echo "  ./ralph.sh --tool claude 10"
echo ""
echo "  # With Amp:"
echo "  ./ralph.sh --tool amp 10"
echo ""
echo -e "${YELLOW}What Ralph will build:${NC}"
echo "  US-001: USB port detection diagnostics"
echo "  US-002: Motor bus connection tester"
echo "  US-003: Individual motor ping utility"
echo "  US-004: Power supply diagnostics"
echo "  US-005: Motor reset/reconfiguration tool"
echo "  US-006: Automated diagnostic report generator"
echo "  US-007: Interactive CLI debugger"
echo ""
echo -e "${GREEN}Good luck debugging your LeRobot servos! 🤖${NC}"
