# Desktop preview packaging control

Frozen before package verification, 2026-09-08.

Desktop version `0.1.0-preview.1` and prospective tag `desktop-v0.1.0-preview.1`
are separate from solver/library/Python version `1.0.1`. No historical scientific
verdict or solver equation changes. Publishing remains a separate approval step.

Build an unsigned Windows installer and portable executable, ad-hoc-signed macOS
application bundles in disk images (Intel and Apple Silicon), and a Linux x86_64
AppImage with a desktop entry. Embed the existing Avila Labs logo as the application
icon and suppress the Windows console in release builds. Pin the packaging tool.

Each package must identify its version, target, exact source commit, binary hash,
and package hashes. Test the shipped executable outside the checkout with an
explicit temporary working directory. The existing P11 synthetic fixture must
open, save, reload, validate its local data paths, solve, and export JSON/CSV using
the desktop model. Reopened result JSON must be exactly equal. Capture all eight
pages and thirteen walkthrough steps where a graphical session is available.
Install/uninstall tests must use disposable runner locations and retain user data.

Keep compile-only, model execution, native rendering, and manual interaction
evidence distinct. Automated captures do not prove native file-dialog interaction
or operating-system download-warning behavior. Keep nuclear fixtures temporary.
Existing Rust gates and the full scientific CI remain required. Prepare a draft
with the actual evidence and remaining limitations; do not auto-publish or tag.
