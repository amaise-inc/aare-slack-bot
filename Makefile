.PHONY: test run lint help

help:
	@echo "Available targets:"
	@echo "  test   Run unit tests"
	@echo "  run    Post to Slack (requires SLACK_WEBHOOK_URL env var)"
	@echo "  lint   Type-check with mypy (if installed)"

test:
	python -m unittest discover -v

run:
	@if [ -z "$$SLACK_WEBHOOK_URL" ]; then \
		echo "ERROR: set SLACK_WEBHOOK_URL first"; exit 1; \
	fi
	python aare_slack.py

lint:
	@command -v mypy >/dev/null 2>&1 || { echo "mypy not installed, skipping"; exit 0; }
	mypy --strict aare_slack.py
