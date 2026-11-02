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

The **0.6.0-preview.1** desktop is unsigned and uses solver **1.7.0**; the CLI and Python packages are at 1.7.2, whose changes reach the desktop in its next preview. It organises setup as numbered steps and adds an Export page that saves a report page, charts and tables; see [What's new in 1.7](whats-new-1.7.md). The Waste class page, which classifies the loaded result against the NRC Part 61 tables, came in 0.5; see [What's new in 1.6](whats-new-1.6.md). The Outputs, Tables, Photons & dose and Pathways pages and FISPACT-II deck import came in 0.4; see [What's new in 1.5](whats-new.md#use-the-04-interface). Its Outputs page has controls for gas production and every uncertainty channel, including the `flux` channel, and the gamma projectile is on Overview & data. Download filenames identify the desktop version and architecture.

For platform launch notices and the optional Linux application-menu shortcut, see the [desktop installation details](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DESKTOP_INSTALL.md). Use the [desktop walkthrough](desktop.md) for calculation setup.

## CLI and Python

On Python 3.9 or newer, install both `import actinv` and the `actinv` terminal command:

```bash
python -m pip install actinv
actinv --version
```

Supported platforms have prebuilt wheels. If a matching wheel is unavailable, pip may attempt a source build; use a [standalone executable](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.7.2) if you want to avoid setting up a compiler.

For a reproducible installation of this handbook's software version:

```bash
python -m pip install actinv==1.7.2
```

If you already use Rust, install just the CLI from crates.io:

```bash
cargo install --locked actinv-cli --version 1.7.2
```

The Python package and `cargo install actinv-cli` install the terminal interface. Get the desktop from the download page.

## Install the nuclear data

Choose a working folder for your problems and run:

```bash
actinv data fetch
actinv data verify
```

The default neutron bundle downloads about 186 MiB and installs about 276 MiB in `actinv-data/v1.2.0/` inside that folder. The first calculation creates a separate prepared-data cache. [Data setup](data.md) explains custom locations, other particles, covariance bundles, and offline installation.

### If `fetch` stops with `http status: 403`

The two decay files (`endfb-viii-0-decay` and `jeff-3-3-decay`) come from the IAEA Nuclear Data Section, whose site
now refuses automated downloads. ACTINV keeps byte-identical copies in the
[decay-sources-v1 release](https://github.com/AvilaLabs/ACTINV/releases/tag/decay-sources-v1). From 1.7.2, `fetch`
takes them from there by itself when the IAEA refuses. With 1.7.1 or earlier, install them once in the same working
folder, then run `fetch` again; it checks them against the catalog and downloads the rest:

```bash
mkdir -p actinv-data/v1.2.0/decay && cd actinv-data/v1.2.0/decay
for f in endf-b-viii-0_decay jeff-3-3_decay; do
  curl -fLO "https://github.com/AvilaLabs/ACTINV/releases/download/decay-sources-v1/$f.sublib.zip"
  unzip -o "$f.sublib.zip" "$f.dat" && rm "$f.sublib.zip"
done
cd ../../.. && actinv data fetch
```

On Windows, download the two `.sublib.zip` files from the release page and extract the `.dat` file inside each into
`actinv-data\v1.2.0\decay\`. An existing `actinv-data` folder from an earlier version already has these files, and
`fetch` reuses them.

Continue with [Your first calculation](quick-start.md).
