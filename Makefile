.PHONY: test lint install uninstall

test:
	python3 -m unittest discover -s tests -v
	tests/test_install.sh

lint:
	shellcheck install.sh tests/test_install.sh
	python3 -m py_compile bin/session-search.py bin/claude-session-emoji.py bin/session-background.py

install:
	./install.sh

uninstall:
	./install.sh --uninstall
