import subprocess
import sys
import platform
from pathlib import Path

# Ensure UTF-8 output even on Windows cp1252 consoles
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

print("=" * 60)
print(f"MARK LI - Setup Environment")
print(f"Python executable: {sys.executable}")
print(f"Python version:    {sys.version.split()[0]} ({platform.platform()})")
print("=" * 60)

print("\n[1/3] Installing requirements from requirements.txt...")
try:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        check=True
    )
    print("✅ Requirements installed successfully.")
except subprocess.CalledProcessError as e:
    print("\n" + "=" * 60)
    print("❌ ERROR: Dependency installation failed!")
    print(f"Exit code: {e.returncode}")
    print(
        "\nCommon causes on Windows:\n"
        "  1. Missing C++ Build Tools (MSVC) if a package lacks a prebuilt wheel.\n"
        "  2. Incompatible Python version for specific binary dependencies.\n"
        "  3. Insufficient disk space on system TEMP drive for large packages (Torch/CUDA).\n"
        "\nCheck the error output above for the specific package that failed.\n"
        "To retry installation manually with full output, run:\n"
        f'  "{sys.executable}" -m pip install -r requirements.txt\n'
    )
    print("=" * 60)
    sys.exit(e.returncode)

print("\n[2/3] Installing Playwright browsers...")
try:
    subprocess.run(
        [sys.executable, "-m", "playwright", "install"],
        check=True
    )
    print("✅ Playwright browsers installed successfully.")
except subprocess.CalledProcessError as e:
    print(
        f"\n⚠️  Warning: Playwright browser installation failed with exit code {e.returncode}.\n"
        "    Web scraping/automation plugins might need browser binaries.\n"
        f'    You can retry manually with: "{sys.executable}" -m playwright install\n'
    )

print("\n[3/3] Checking platform-specific configurations...")
if platform.system() == "Windows":
    try:
        import win32com.client  # noqa: F401
        print("✅ pywin32 COM integration available.")
    except ImportError:
        postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
        print(
            "\n⚠️  pywin32 did not install correctly — desktop shortcut creation "
            "will fall back to a slower method that may not work on this machine.\n"
            "    Try fixing it manually with:\n"
            f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
            f'    "{sys.executable}" "{postinstall}" -install\n'
        )

print("\n" + "=" * 60)
print("✅ Setup complete! Run 'python main.py' to start MARK LI.")
print("=" * 60 + "\n")


