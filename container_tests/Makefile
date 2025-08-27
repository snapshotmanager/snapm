IMAGE = container-tests
CONTAINER = container-tests
TEST_DIR = $(CURDIR)/tests

COVERAGE_INCLUDE = /src/snapm/manager/_schedule.py,/src/snapm/manager/_timers.py

.PHONY: all prep build run install test clean shell

all: prep build run install test

prep:
	cp $(CURDIR)/../systemd/*.service $(CURDIR)
	cp $(CURDIR)/../systemd/*.timer $(CURDIR)
	cp $(CURDIR)/../systemd/tmpfiles.d/snapm.conf $(CURDIR)

build:
	podman build -t $(IMAGE) .

run:
	podman run --rm --detach --privileged \
		--name $(CONTAINER) \
		--volume $(TEST_DIR):/tests \
		--volume $(CURDIR)/../:/src \
		--volume /sys/fs/cgroup:/sys/fs/cgroup:ro \
		$(IMAGE)

install:
	podman exec -it $(CONTAINER) /usr/bin/pip install /src

test:
	podman exec --env PYTHONPATH=/src -it $(CONTAINER) \
		/usr/bin/coverage run \
		--include $(COVERAGE_INCLUDE) \
		/usr/bin/pytest -v --log-level=debug /tests

report:
	podman exec --env PYTHONPATH=/src -it $(CONTAINER) \
		/usr/bin/coverage report -m

clean:
	podman rm -f $(CONTAINER) 2>/dev/null || true
	rm -f $(CURDIR)/*.service
	rm -f $(CURDIR)/*.timer
	rm -f $(CURDIR)/snapm.conf

shell:
	podman exec -it $(CONTAINER) /usr/bin/bash
