PY := PYTHONPATH=src python3 -m vt_runtime
MOZA := configs/moza_song_screening.json

.PHONY: demo demo-crash-a demo-crash-b demo-crash-b-down demo-approval demo-two-runs inspect reset test

demo: reset
	$(PY) run --config $(MOZA) --run-id run_demo
	@echo
	$(PY) inspect

demo-crash-a: reset
	@echo "--- attempt 1: crash AFTER intent, BEFORE external call ---"
	-$(PY) run --config $(MOZA) --run-id run_crash_a --crash-at A
	@echo "--- retry: should create the task exactly once ---"
	$(PY) run --config $(MOZA) --run-id run_crash_a
	@echo
	$(PY) inspect

demo-crash-b: reset
	@echo "--- attempt 1: crash AFTER external call, BEFORE commit ---"
	-$(PY) run --config $(MOZA) --run-id run_crash_b --crash-at B
	@echo "--- retry: must NOT create a second task ---"
	$(PY) run --config $(MOZA) --run-id run_crash_b
	@echo
	$(PY) inspect

demo-crash-b-down: reset
	@echo "--- crash after external call, then retry with external DOWN ---"
	-$(PY) run --config $(MOZA) --run-id run_crash_b --crash-at B
	$(PY) run --config $(MOZA) --run-id run_crash_b --external down || true
	@echo
	$(PY) inspect

demo-approval: reset
	@echo "--- approval required: external action waits, one config flag, no code ---"
	$(PY) run --config $(MOZA) --run-id run_appr --approval required
	@echo
	$(PY) inspect

demo-two-runs: reset
	@echo "--- two DIFFERENT runs, same songs: two tasks is correct ---"
	$(PY) run --config $(MOZA) --run-id run_100
	$(PY) run --config $(MOZA) --run-id run_101
	@echo
	$(PY) inspect

inspect:
	$(PY) inspect

reset:
	rm -rf state

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v
