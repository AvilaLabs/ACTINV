# Install ACTINV

Choose the desktop for a graphical workflow, or install the CLI and Python package for scripts and notebooks. Nuclear data are installed separately.

## Desktop

Open the [ACTINV download page](https://actinv.avilalabs.org/download/) and choose the package for your computer. No Rust or Python installation is required.

| Computer | Package | First launch |
| --- | --- | --- |
| Windows, Intel or AMD 64-bit | Installer or portable ZIP | Install and open ACTINV from Start; for the ZIP, extract it and open `ACTINV.exe` |
| Mac, Apple Silicon | Apple Silicon disk image | Open the DMG and drag ACTINV to Applications |
| Mac, Intel | Intel disk image | Open the DMG and drag ACTINV to Applications |
| Linux, Intel or AMD 64-bit | AppImage | Allow execution in file properties, then double-click |

The published **0.1.0-preview.1** desktop is unsigned and uses solver **1.0.1**. The CLI/Python software release is **1.4.0**; features added afterward require a newer desktop build or the current CLI/Python package. Download filenames identify the desktop version and architecture.

For platform launch notices and the optional Linux application-menu shortcut, see the [desktop installation details](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DESKTOP_INSTALL.md). Use the [desktop walkthrough](desktop.md) for calculation setup.

## CLI and Python

On Python 3.9 or newer, install both `import actinv` and the `actinv` terminal command:

```bash
python -m pip install actinv
actinv --version
```

Supported platforms have prebuilt wheels. If a matching wheel is unavailable, pip may attempt a source build; use a [standalone executable](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.4.0) if you want to avoid setting up a compiler.

For a reproducible installation of this handbook's software version:

```bash
python -m pip install actinv==1.4.0
```

If you already use Rust, install just the CLI from crates.io:

```bash
cargo install --locked actinv-cli --version 1.4.0
```

The Python package and `cargo install actinv-cli` install the terminal interface. Get the desktop from the download page.

## Install the nuclear data

Choose a working folder for your problems and run:

```bash
actinv data fetch
actinv data verify
```

The default neutron bundle downloads about 79 MiB and installs about 170 MiB in `actinv-data/v1.1.0/` inside that folder. The first calculation creates a separate prepared-data cache. [Data setup](data.md) explains custom locations, other particles, covariance bundles, and offline installation.

Continue with [Your first calculation](quick-start.md).
