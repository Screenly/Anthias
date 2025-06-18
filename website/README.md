# Anthias Website

This is the source code for [anthias.screenly.io](https://anthias.screenly.io).

## Local Development

To run the website locally:

```bash
# Start the development server
docker compose -f docker-compose.website.yml up --build

# The website will be available at:
# http://localhost:8080
```

## Project Structure

```
website/
├── assets/                      # Static assets (images, styles)
├── docker-compose.website.yml   # Docker Compose file for local development
└── index.html                   # Main website content
```
