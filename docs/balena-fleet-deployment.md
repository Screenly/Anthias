# Deploying on a Balena fleet

## Introduction

You can use the Balena disk images provided in the releases page to install
Anthias in your device. However, if you want more control and transparency over
the Balena deployment, follow through the steps below to deploy Anthias on your
Balena fleet.

## Prerequisites

- Create a [balenaCloud](https://www.balena.io/cloud) account if you don't have
  one already.
- Install the [balena CLI](https://docs.balena.io/reference/balena-cli/) on your
  computer. It's recommended to install it on a Linux machine but it should work
  on Windows and macOS as well. If you're on Windows, you can use the Windows
  Subsystem for Linux (WSL) to install the CLI. Click
  [here](https://github.com/balena-io/balena-cli/blob/master/INSTALL.md) for the
  installation instructions.
- On your terminal, login to your balenaCloud account using the CLI by running
  `balena login`.
- Generate an API key for your balenaCloud account by going to your account
  settings page and clicking on the `Create API Key` button. Copy the API key
  and save it somewhere safe. You'll need it later. Alternatively, you can run
  `balena api-key generate <name>` to generate an API key using the CLI.
