PY ?= python

.PHONY: test validate

test:
	$(PY) -m unittest discover -s tests -p 'test_*.py'

validate: test
	@test -n "$(DATASET)" || (echo "DATASET is required" >&2; exit 2)
	@test -n "$(QUERIES)" || (echo "QUERIES is required" >&2; exit 2)
	VAL_AGENT=agents.stage1_resource $(PY) scripts/validate_submission.py \
		--submission . --dataset "$(DATASET)" --queries "$(QUERIES)"
