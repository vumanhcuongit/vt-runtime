PY := python3 cli.py
MOZA := workflows/moza_song_screening/config.json
HELIOS := workflows/helios_recruiting_screening/config.json
# crash demos use a single-include-song source so the story is crisp:
# "one task should exist; after crash + retry, exactly one does -- not two".
CRASH_SRC := workflows/moza_song_screening/fixtures/songs_crash.json
MISSING_SRC := workflows/moza_song_screening/fixtures/songs_missing_id.json

.PHONY: preflight demo demo-crash-a demo-crash-b demo-crash-b-down demo-approval demo-two-runs demo-missing-id demo-helios inspect reset test

# Verify the only requirement (Python 3 with its bundled sqlite3) before any
# demo. SQLite ships inside CPython; this only ever fails on a hand-built
# Python compiled without SQLite -- never on a downloaded/prebuilt one.
preflight:
	@command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found. Install Python 3.12+ from python.org."; exit 1; }
	@python3 -c "import sqlite3" 2>/dev/null || { echo "ERROR: this Python lacks the bundled sqlite3 module (rare: a source build without SQLite). Use a standard Python 3.12+ from python.org."; exit 1; }
	@echo "preflight OK: python3 $$(python3 -V 2>&1 | cut -d' ' -f2), bundled sqlite3 $$(python3 -c 'import sqlite3;print(sqlite3.sqlite_version)'), zero external deps"

demo: preflight reset
	$(PY) run --config $(MOZA) --run-id run_demo
	@echo
	$(PY) inspect

demo-crash-a: preflight reset
	@echo "--- attempt 1: crash AFTER intent, BEFORE external call ---"
	-$(PY) run --config $(MOZA) --source $(CRASH_SRC) --run-id run_crash_a --crash-at A
	@echo "--- retry: should create the task exactly once ---"
	$(PY) run --config $(MOZA) --source $(CRASH_SRC) --run-id run_crash_a
	@echo
	$(PY) inspect

demo-crash-b: preflight reset
	@echo "--- attempt 1: crash AFTER external call, BEFORE commit ---"
	-$(PY) run --config $(MOZA) --source $(CRASH_SRC) --run-id run_crash_b --crash-at B
	@echo "--- retry: must NOT create a second task ---"
	$(PY) run --config $(MOZA) --source $(CRASH_SRC) --run-id run_crash_b
	@echo
	$(PY) inspect

demo-crash-b-down: preflight reset
	@echo "--- crash after external call, then retry with external DOWN ---"
	-$(PY) run --config $(MOZA) --source $(CRASH_SRC) --run-id run_crash_b --crash-at B
	$(PY) run --config $(MOZA) --source $(CRASH_SRC) --run-id run_crash_b --external down || true
	@echo
	$(PY) inspect

demo-approval: preflight reset
	@echo "--- the announced change request: force approval ON, no code change ---"
	@echo "    (the action waits at 'pending'; granting it is a disclosed gap)"
	$(PY) run --config $(MOZA) --run-id run_appr --require-approval
	@echo
	$(PY) inspect

demo-two-runs: preflight reset
	@echo "--- two DIFFERENT runs, same songs: two tasks is correct ---"
	$(PY) run --config $(MOZA) --run-id run_100
	$(PY) run --config $(MOZA) --run-id run_101
	@echo
	$(PY) inspect

demo-missing-id: preflight reset
	@echo "--- item missing its song_id: run must stop at fetch, no index fallback ---"
	$(PY) run --config $(MOZA) --run-id run_missing --source $(MISSING_SRC)
	@echo
	$(PY) inspect

demo-helios: preflight reset
	@echo "--- SAME runner, DIFFERENT VT: helios recruiting screening ---"
	@echo "    (config declares approval: required -- the advancing candidate's"
	@echo "     note halts at 'pending'; this is the honest half-built approval gate)"
	$(PY) run --config $(HELIOS) --run-id run_helios
	@echo
	$(PY) inspect

inspect: preflight
	$(PY) inspect

reset:
	rm -rf state

test: preflight
	python3 -m unittest discover -s tests -v
