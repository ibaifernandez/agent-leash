.PHONY: test mutants all

all: test mutants

test:
	@python3 tests/seal.py

mutants:
	@python3 tests/mutants.py
