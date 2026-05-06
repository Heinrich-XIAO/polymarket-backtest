.PHONY: setup sync seed test demo logs clean

# Start all services and build images
setup:
	docker compose up --build -d
	@echo ""
	@echo "  Backend API:  http://localhost:8000/docs"
	@echo "  Frontend UI:  http://localhost:3000"
	@echo ""
	@echo "  Next: run 'make sync' to load market data, or 'make seed' for demo data."

# Pull live data from Polymarket Gamma API (200 markets, runs in background)
sync:
	curl -s -X POST "http://localhost:8000/admin/sync?max_markets=200" | python3 -m json.tool

# Seed database with synthetic data for demo/testing (no internet needed)
seed:
	docker compose exec backend python /app/scripts/seed_markets.py

# Run backend unit tests
test:
	docker compose exec backend pytest tests/ -v --tb=short

# Open the UI in the default browser
demo:
	@echo "Opening http://localhost:3000 ..."
	@python3 -c "import webbrowser; webbrowser.open('http://localhost:3000')" 2>/dev/null || \
	 open http://localhost:3000 2>/dev/null || \
	 start http://localhost:3000

# Check health
health:
	curl -s http://localhost:8000/health | python3 -m json.tool

# Tail logs for all services
logs:
	docker compose logs -f

# Stop and remove containers (keeps DB volume)
down:
	docker compose down

# Full reset — removes DB volume too
clean:
	docker compose down -v
	@echo "All containers and volumes removed."
