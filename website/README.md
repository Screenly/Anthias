# Anthias Website

This is the source code for [anthias.screenly.io](https://anthias.screenly.io), built with [Hugo](https://gohugo.io/).

## Local Development

```bash
hugo server --source website
```

The website will be available at http://localhost:1313.

## Build

```bash
hugo --source website
```

Output goes to `website/public/`.

## Project Structure

```
website/
├── assets/                      # Static assets (images, styles)
├── content/
│   └── _index.md                # Home page content
├── layouts/
│   ├── _default/
│   │   └── baseof.html          # Base HTML template
│   ├── partials/                # Reusable template partials
│   │   ├── navbar.html
│   │   ├── hero.html
│   │   ├── features.html
│   │   ├── faq.html
│   │   └── footer.html
│   └── index.html               # Home page layout
├── hugo.toml                    # Hugo configuration
└── public/                      # Build output (git-ignored)
```
