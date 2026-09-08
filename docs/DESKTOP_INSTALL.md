# Install ACTINV Desktop preview

Desktop **0.1.0-preview.1** uses solver **1.0.1**. Desktop packaging has its own
version and `desktop-v…` tags; it does not change the scientific release verdicts.
The candidate is not published yet. Maintainers can download candidate packages
from successful [desktop builds](https://github.com/AvilaLabs/ACTINV/actions/workflows/desktop.yml).
Public downloads will appear on [GitHub Releases](https://github.com/AvilaLabs/ACTINV/releases)
after review. The older v1.0.1 release does not contain the desktop.

## Choose a download

| Computer | Package | Launch |
|---|---|---|
| Windows x86_64 | `windows-x86_64-setup.exe` | Install for your user, then open ACTINV from Start |
| Windows x86_64, portable | `windows-x86_64-portable.zip` | Extract the ZIP, then double-click `ACTINV.exe` |
| Apple Silicon Mac | `macos-aarch64.dmg` | Open disk image, drag ACTINV to Applications, then open it |
| Intel Mac | `macos-x86_64.dmg` | Same steps using the Intel download |
| Linux x86_64 | `linux-x86_64.AppImage` | Allow execution in file properties, then double-click |

All filenames start with `ACTINV-Desktop-0.1.0-preview.1-`. No Rust compiler or
Python installation is needed to run the application. macOS packages target 12+
and Linux packages are built on Ubuntu 22.04; actual test platforms are recorded
in each package's verification file. A working graphics driver is required.

## First-launch notices

This preview has **no trusted publisher signature**. Windows downloads may show
SmartScreen's unrecognized-app warning. After confirming the file came from this
repository, use **More info → Run anyway**, if your computer allows it.
[Microsoft explains SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

macOS bundles have an ad-hoc integrity signature, not an Apple Developer ID
signature, and are not notarized. Try opening ACTINV, then use **System Settings →
Privacy & Security → Open Anyway** if macOS blocks it.
[Apple's instructions](https://support.apple.com/en-us/102445) explain the process.
Managed-device policy or Windows Smart App Control may prevent an override.
Do not turn off system-wide protection to run the preview.

## Linux application-menu shortcut

Keep the AppImage, `install-linux.sh`, and `actinv.png` from the same download
together, then run `bash install-linux.sh` once. It installs a user-local copy
under `${XDG_DATA_HOME:-$HOME/.local/share}/actinv-desktop` and adds an application
menu entry. No administrator privileges are needed. Thereafter launch from the
menu. To remove it, delete that directory and
`${XDG_DATA_HOME:-$HOME/.local/share}/applications/com.avilalabs.actinv.desktop`.
Your nuclear data, problems, and exported results are separate and remain intact.

Some distributions need FUSE 2 compatibility to launch AppImages. If it is
unavailable, run `./ACTINV-Desktop-…AppImage --appimage-extract`, then launch
`squashfs-root/AppRun`. Linux file dialogs use the desktop's XDG portal service.

## First calculation

1. Open ACTINV and choose **Overview & data**.
2. Choose a writable folder for the verified data download. Data stays separate
   from the installed application; never place it inside the macOS bundle.
3. Load the iron example or open your problem. Choose the input base folder and
   confirm the library and decay paths.
4. Review material, irradiation/cooling, and spectrum; then **Validate** and **Run**.
5. Explore **Results** and export JSON or CSV. Use **Help** for highlighted
   walkthroughs of setup and results.

## Verification and preview limits

Each download includes a `build-*.json` with the source commit and binary hash,
a `verification-*.json` distinguishing model execution from native rendering,
and a `SHA256SUMS-*.txt` for integrity checks. On Windows use `Get-FileHash`;
on macOS use `shasum -a 256`; on Linux use `sha256sum -c SHA256SUMS-*.txt`.

Automated package checks exercise real executables with temporary synthetic
inputs. Screenshot automation covers eight pages and thirteen Help steps when
the runner supports rendering. It does not simulate native file-picker clicks,
browser download warnings, or every desktop/driver combination. See the exact
verification files and release notes before treating a platform as tested.

The desktop currently supports single-material calculations. Mesh execution,
transport imports, and bulk library building remain CLI workflows. All existing
[scientific limitations](QUALIFICATION.md) still apply.
