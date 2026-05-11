.PHONY: install test lint typecheck run-scenarios grade-local demo-hitl-start demo-hitl-resume demo-time-travel demo-crash-start demo-crash-resume demo-hitl-ui clean

install:
	pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check src tests

typecheck:
	mypy src

run-scenarios:
	python -m langgraph_agent_lab.cli run-scenarios --config configs/lab.yaml --output outputs/metrics.json

grade-local:
	python -m langgraph_agent_lab.cli validate-metrics --metrics outputs/metrics.json

demo-hitl-start:
	python -m langgraph_agent_lab.cli demo-hitl-start --config configs/lab.yaml --thread demo-hitl

demo-hitl-resume:
	python -m langgraph_agent_lab.cli demo-hitl-resume --config configs/lab.yaml --thread-id thread-demo-hitl --action approve --comment "approved in demo"

demo-time-travel:
	python -m langgraph_agent_lab.cli demo-time-travel --config configs/lab.yaml --thread-id thread-demo-hitl --limit 10

demo-crash-start:
	python -m langgraph_agent_lab.cli demo-crash-recover --config configs/lab.yaml --phase start --thread demo-crash

demo-crash-resume:
	python -m langgraph_agent_lab.cli demo-crash-recover --config configs/lab.yaml --phase resume --thread demo-crash --action approve

demo-hitl-ui:
	streamlit run src/langgraph_agent_lab/hitl_ui.py

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov dist build *.egg-info outputs/*.json
