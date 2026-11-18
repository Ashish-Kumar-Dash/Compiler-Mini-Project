# Thin wrapper: the real task runner is tasks.py (works on Windows without make).
PY ?= python3
.PHONY: test golden fuzz diff cosim arduino demo
test golden fuzz diff cosim arduino demo:
	$(PY) tasks.py $@
