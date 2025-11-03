# Botcrypto4 Monorepo Scaffold

This repository provides a minimal monorepo scaffold featuring a Next.js 14 frontend and a FastAPI backend, wired together with Docker for local development.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose)
- (Optional) Make if you prefer the provided make targets

## Getting Started

1. **Clone the repository** and move into the project directory.

2. **Configure environment variables**:

   ```bash
   cp .env.example .env
   cp frontend/.env.local.example frontend/.env.local
   cp backend/.env.example backend/.env
   ```

   You can edit the copied files to customise ports or API URLs. By default the frontend expects the backend at `http://localhost:8000` when running outside Docker, and `http://backend:8000` when running inside Docker Compose.

3. **Start the stack**:

   ```bash
   docker compose up --build
   ```

   Or, using the provided make targets:

   ```bash
   make up
   ```

4. **Verify everything is running**:

   - Frontend: <http://localhost:3000>
   - Backend health: <http://localhost:8000/health> (returns `{ "status": "ok" }`)

   The homepage displays the backend health in real time by calling the FastAPI `/health` endpoint.

## Project Structure

```
.
├── backend
│   ├── Dockerfile
│   ├── app
│   │   ├── __init__.py
│   │   └── main.py
│   ├── .env.example
│   └── requirements.txt
├── frontend
│   ├── Dockerfile
│   ├── app
│   │   ├── globals.css
│   │   ├── layout.tsx
│   │   └── page.tsx
│   ├── .env.local.example
│   ├── next-env.d.ts
│   ├── next.config.js
│   ├── package.json
│   ├── tsconfig.json
│   └── .eslintrc.json
├── docker-compose.yml
├── .env.example
├── .gitignore
├── Makefile
├── README.md
└── .prettierrc
```

## Scripts

Inside `frontend`:

- `npm run dev` – Start Next.js dev server (binds to `0.0.0.0:3000`)
- `npm run build` – Production build
- `npm run start` – Start Next.js in production mode
- `npm run lint` – Run ESLint
- `npm run format` – Format the project with Prettier

Inside `backend` (outside Docker):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Stopping & Logs

- Stop services: `docker compose down` or `make down`
- Follow logs: `docker compose logs -f` or `make logs`

## Notes

- The backend enables CORS for `http://localhost:3000` by default. Adjust `CORS_ALLOW_ORIGINS` in `backend/.env` if you need to serve the frontend from a different origin.
- `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000` so the frontend can reach the backend when running outside Docker. When running within Docker Compose, the service is set to `http://backend:8000` automatically.
