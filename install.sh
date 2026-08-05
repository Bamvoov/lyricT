#!/usr/bin/env bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Installing lyricT...${NC}"

# 1. Check Python installation
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Error: python3 is required but not installed.${NC}"
    exit 1
fi

# 2. Check playerctl installation
if ! command -v playerctl &> /dev/null; then
    echo -e "${YELLOW}⚠️  Warning: 'playerctl' is not installed. lyricT requires playerctl to query music players.${NC}"
    echo -e "   Please install it using your package manager, e.g.:"
    echo -e "   - Arch Linux: ${BLUE}sudo pacman -S playerctl${NC}"
    echo -e "   - Debian/Ubuntu: ${BLUE}sudo apt install playerctl${NC}"
    echo -e "   - Fedora: ${BLUE}sudo dnf install playerctl${NC}"
fi

# 3. Create destination directory
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

# 4. Download or copy the main script
SCRIPT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lyricT.py"
if [ -f "$SCRIPT_SRC" ]; then
    # Installed from a cloned repository
    cp "$SCRIPT_SRC" "$BIN_DIR/lyricT"
else
    # Installed via curl
    GITHUB_RAW_URL="https://raw.githubusercontent.com/Bamvoov/lyricT/main/lyricT.py"
    echo -e "${BLUE}Downloading script from GitHub...${NC}"
    if ! curl -sSL "$GITHUB_RAW_URL" -o "$BIN_DIR/lyricT"; then
        echo -e "${RED}❌ Error: Failed to download lyricT.py from GitHub.${NC}"
        exit 1
    fi
fi

# Make executable
chmod +x "$BIN_DIR/lyricT"

# 5. Install Python dependencies
echo -e "${BLUE}📦 Installing Python dependencies (requests, pillow)...${NC}"

install_deps() {
    python3 -m pip install requests pillow --break-system-packages --user &> /dev/null || \
    python3 -m pip install requests pillow --user &> /dev/null || \
    pip install requests pillow --user &> /dev/null || \
    pip3 install requests pillow &> /dev/null
}

if install_deps; then
    echo -e "${GREEN}✅ Python dependencies installed successfully.${NC}"
else
    echo -e "${YELLOW}⚠️  Warning: Failed to install Python dependencies automatically.${NC}"
    echo -e "   Please try installing them manually: ${BLUE}pip install requests pillow${NC}"
fi

echo -e "\n${GREEN}🎉 lyricT installed successfully to ${BIN_DIR}/lyricT!${NC}"

# Check if PATH contains ~/.local/bin
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo -e "💡 Ensure that ${BLUE}~/.local/bin${NC} is in your PATH."
    case "$(basename "${SHELL:-}")" in
        fish)
            echo -e "   Since you are using fish shell, run this command to add it permanently:"
            echo -e "   ${BLUE}fish_add_path ~/.local/bin${NC}"
            ;;
        zsh)
            echo -e "   If it isn't, add the following line to your ${BLUE}~/.zshrc${NC}:"
            echo -e "   ${BLUE}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
            ;;
        bash)
            echo -e "   If it isn't, add the following line to your ${BLUE}~/.bashrc${NC}:"
            echo -e "   ${BLUE}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
            ;;
        *)
            echo -e "   If it isn't, add the following line to your shell configuration file (e.g., ~/.bashrc or ~/.zshrc):"
            echo -e "   ${BLUE}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
            ;;
    esac
fi

echo -e "\nRun the program with: ${GREEN}lyricT${NC}"

