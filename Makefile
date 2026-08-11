.PHONY: setup sync seed test run frontend demo logs health down clean

# Create the uv venv and install backend dependencies (no Docker)
setup:
	cd backend && uv sync
	@echo ""
	@echo "  Next: 'make run' to start the API, or 'make seed' for demo data."

# Run the backend API directly with uv (no Docker)
run:
	cd backend && uv run uvicorn main:app --reload --port 8000

# Run the frontend dev server
frontend:
	cd frontend && bun dev

# Pull live data from Polymarket Gamma API (1200 markets, runs in background)
sync:
	curl -s -X POST "http://localhost:8000/admin/sync?max_markets=1200" | python3 -m json.tool

# Seed database with synthetic data for demo/testing (no internet needed)
seed:
	cd backend && uv run python ../scripts/seed_markets.py

# Run backend unit tests
test:
	cd backend && uv run pytest tests/ -v --tb=short

# Open the UI in the default browser
demo:
	@echo "Opening http://localhost:3000 ..."
	@python3 -c "import webbrowser; webbrowser.open('http://localhost:3000')" 2>/dev/null || \
	 open http://localhost:3000 2>/dev/null || \
	 start http://localhost:3000

# Check health
health:
	curl -s http://localhost:8000/health | python3 -m json.tool

# Tail backend logs
logs:
	cd backend && uv run uvicorn main:app --port 8000 --log-level info

# Remove the local SQLite database (full reset)
clean:
	rm -f backend/data/polymarket.db
	@echo "Database removed."
