<h1 align="center" style="border-bottom: none;">
    Anthias
    &middot;
    Open Source Digital Signage Solution for Raspberry Pi and PC
</h1>

<p align="center">
  <a href="https://github.com/Screenly/Anthias/actions/workflows/docker-test.yaml?query=branch%3Amaster"><img src="https://img.shields.io/github/actions/workflow/status/Screenly/Anthias/docker-test.yaml?branch=master&style=for-the-badge&label=Run%20Unit%20Tests" alt="Run Unit Tests"></a>
  <a href="https://github.com/Screenly/Anthias/actions/workflows/codeql-analysis.yaml?query=branch%3Amaster"><img src="https://img.shields.io/github/actions/workflow/status/Screenly/Anthias/codeql-analysis.yaml?branch=master&style=for-the-badge&label=CodeQL" alt="CodeQL"></a>
  <a href="https://github.com/Screenly/Anthias/actions/workflows/python-lint.yaml?query=branch%3Amaster"><img src="https://img.shields.io/github/actions/workflow/status/Screenly/Anthias/python-lint.yaml?branch=master&style=for-the-badge&label=Run%20Python%20Linter" alt="Run Python Linter"></a>
  <br>
  <a href="https://github.com/Screenly/Anthias/releases/latest?query=branch%3Amaster"><img src="https://img.shields.io/github/v/release/Screenly/Anthias?style=for-the-badge&color=8A2BE2" alt="GitHub release (latest by date)"></a>
  <a href="https://app.sbomify.com/project/ENyjfn8tXQ"><img src="https://img.shields.io/badge/_-sbomified-8A2BE2?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjU3IiBoZWlnaHQ9IjI1NyIgdmlld0JveD0iMCAwIDI1NyAyNTciIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxjaXJjbGUgY3g9IjEyOC41IiBjeT0iMTI4LjUiIHI9IjEyOC41IiBmaWxsPSIjMTQxMDM1Ii8+CjxwYXRoIGQ9Ik02My45Mzc5IDgwLjAwMDlDNTcuNTM3NCA3OS45MTMyIDU3LjUyNDkgODkuOTk4OSA2My45Mzc5IDg5LjkxMTJIOTcuNzI4NUMxMDQuMTU0IDkwLjAyNCAxMDQuMTY3IDc5Ljg4ODIgOTcuNzI4NSA4MC4wMDA5SDYzLjkzNzlaTTExMy43OCA4MC4wMDA5QzEwNy40MDQgNzkuOTEzMiAxMDcuMzY3IDg5Ljk5ODkgMTEzLjc4IDg5LjkxMTJIMTk0LjA3NUMxOTYuOCA4OS45MTEyIDE5OSA4Ny42OTM2IDE5OSA4NC45NzQ5QzE5OSA4Mi4yMzExIDE5Ni44IDgwLjAxMzUgMTk0LjA3NSA4MC4wMTM1SDExMy43OFY4MC4wMDA5Wk02My45Mzc5IDk3LjE0MDRDNTcuNTQ5OSA5Ny4wNTI3IDU3LjUxMjQgMTA3LjE1MSA2My45Mzc5IDEwNy4wNTFIMTE5LjM1NUMxMjIuMDgxIDEwNy4wNTEgMTI0LjI4MSAxMDQuODMzIDEyNC4yODEgMTAyLjEwMkMxMjQuMjkzIDk5LjM3MDUgMTIyLjA4MSA5Ny4xNDA0IDExOS4zNTUgOTcuMTQwNEg2My45Mzc5Wk02My45Mzc5IDExNC4zMDVDNTcuNTYyNCAxMTQuMjE3IDU3LjUxMjQgMTI0LjI3OCA2My45Mzc5IDEyNC4xOUgxNDQuNjdDMTQ3LjM5NSAxMjQuMTkgMTQ5LjU5NiAxMjEuOTcyIDE0OS41OTYgMTE5LjI1NEMxNDkuNTk2IDExNi41MjIgMTQ3LjM4MyAxMTQuMzE3IDE0NC42NyAxMTQuMzE3SDYzLjkzNzlWMTE0LjMwNVpNMTk0LjA3NSAxNzYuOTk5QzIwMC40NzUgMTc3LjA4NyAyMDAuNDg4IDE2Ny4wMDEgMTk0LjA3NSAxNjcuMDg5SDE2MC4yODRDMTUzLjg1OCAxNjYuOTc2IDE1My44NDYgMTc3LjExMiAxNjAuMjg0IDE3Ni45OTlIMTk0LjA3NVpNMTQ0LjIyIDE3Ni45OTlDMTUwLjU5NiAxNzcuMDg3IDE1MC42MzMgMTY3LjAwMSAxNDQuMjIgMTY3LjA4OUg2My45MjU0QzYxLjIxMjcgMTY3LjEwMSA1OSAxNjkuMzA2IDU5IDE3Mi4wMzhDNTkgMTc0Ljc4MSA2MS4yMDAyIDE3Ni45OTkgNjMuOTI1NCAxNzYuOTk5SDE0NC4yMlpNMTk0LjA3NSAxNTkuODZDMjAwLjQ2MyAxNTkuOTQ3IDIwMC41IDE0OS44NDkgMTk0LjA3NSAxNDkuOTQ5SDEzOC42NTdDMTM1LjkzMiAxNDkuOTQ5IDEzMy43MzIgMTUyLjE2NyAxMzMuNzMyIDE1NC44OThDMTMzLjcxOSAxNTcuNjMgMTM1LjkzMiAxNTkuODYgMTM4LjY1NyAxNTkuODZIMTk0LjA3NVpNMTk0LjA3NSAxNDIuNjk1QzIwMC40NSAxNDIuNzgzIDIwMC41IDEzMi43MjIgMTk0LjA3NSAxMzIuODFIMTEzLjM0MkMxMTAuNjE3IDEzMi44MSAxMDguNDE3IDEzNS4wMjggMTA4LjQxNyAxMzcuNzQ2QzEwOC40MTcgMTQwLjQ3OCAxMTAuNjMgMTQyLjY4MyAxMTMuMzQyIDE0Mi42ODNIMTk0LjA3NVYxNDIuNjk1WiIgZmlsbD0idXJsKCNwYWludDBfbGluZWFyXzM5Nl8yOTcpIi8+CjxkZWZzPgo8bGluZWFyR3JhZGllbnQgaWQ9InBhaW50MF9saW5lYXJfMzk2XzI5NyIgeDE9IjU5IiB5MT0iMTI4LjUiIHgyPSIyMDIuMjMzIiB5Mj0iMTQxLjU2NyIgZ3JhZGllbnRVbml0cz0idXNlclNwYWNlT25Vc2UiPgo8c3RvcCBvZmZzZXQ9IjAuMDYiIHN0b3AtY29sb3I9IiM0MDU5RDAiLz4KPHN0b3Agb2Zmc2V0PSIwLjU1NSIgc3RvcC1jb2xvcj0iI0NDNThCQiIvPgo8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiNGNEI1N0YiLz4KPC9saW5lYXJHcmFkaWVudD4KPC9kZWZzPgo8L3N2Zz4K"></a>
</p>

<br>

![Anthias Logo](https://github.com/Screenly/Anthias/blob/master/static/img/color.svg?raw=true  "Anthias Logo")

<br>

## :sparkles: About Anthias

Anthias is a digital signage platform for Raspberry Pi devices and PCs. Formerly known as Screenly OSE, it was rebranded to clear up the confusion between Screenly (the paid version) and Anthias. More details can be found in [this blog post](https://www.screenly.io/blog/2022/12/06/screenly-ose-now-called-anthias/).

:tada: **NEW: Now with Raspberry Pi 5 Support!** :tada:

Want to help Anthias thrive? Support us using [GitHub Sponsor](https://github.com/sponsors/Screenly).

## :rocket: Getting Started

See [this](/docs/installation-options.md) page for options on how to install Anthias.

## :white_check_mark: Compatibility

> [!WARNING]
> Anthias does not currently support devices running Debian Trixie.
> Please use Debian Bookworm or Raspberry Pi OS Bookworm for the best experience.

### balenaOS

> [!NOTE]
> See [this](/docs/installation-options.md) page for instructions on how to install Anthias on balenaOS.
> You can either use the [images from balenaHub](/docs/installation-options.md#using-the-images-from-balenahub)
> or [download the images from the releases](/docs/installation-options.md#using-the-images-from-the-releases).

### Raspberry Pi OS

* Raspberry Pi 5 Model B - 64-bit Bookworm **(NEW!)**
* Raspberry Pi 4 Model B - 32-bit and 64-bit Bullseye, 64-bit Bookworm
* Raspberry Pi 3 Model B+ - 32-bit and 64-bit Bullseye, 64-bit Bookworm
* Raspberry Pi 3 Model B - 64-bit Bookworm and Bullseye
* Raspberry Pi 2 Model B - 32-bit Bookworm and Bullseye
* PC (x86 Devices) - 64-bit Bookworm
  * These devices can be something similar to a NUC.
  * See [this](/docs/x86-installation.md) page for instructions on how to install Debian in a specific way
    before running the [installation script](/docs/installation-options.md#installing-on-raspberry-pi-os-lite-or-debian).

> [!NOTE]
> We're still fixing the Raspberry Pi OS installer so that it'll work with Raspberry Pi Zero and Raspberry Pi 1.
> Should you encounter any issues, please file an issue either in this repository or in the
[forums](https://forums.screenly.io).

## :star: Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Screenly/Anthias&type=Date)](https://star-history.com/#Screenly/Anthias&Date)

## :lady_beetle: Issues and Bugs

> [!NOTE]
> We are still in the process of knocking out some bugs. You can track the known issues [here](https://github.com/Screenly/Anthias/issues). You can also check the discussions in the [Anthias forums](https://forums.screenly.io).

## :zap: Quick Links

* [Forum](https://forums.screenly.io/)
* [Website](https://anthias.screenly.io) (hosted on GitHub and the source is available [here](/website))
* [General documentation](https://github.com/Screenly/Anthias/blob/master/docs/README.md)
* [Developer documentation](https://github.com/Screenly/Anthias/blob/master/docs/developer-documentation.md)
* [Migrating assets from Anthias to Screenly](/docs/migrating-assets-to-screenly.md)
* [WebView](/webview/README.md)
