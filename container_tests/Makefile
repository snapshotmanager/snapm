IMAGE = container-tests
CONTAINER = container-tests
TEST_DIR = $(CURDIR)/tests
TEST_UNIT = container-tests

.PHONY: all build run status logs clean shell

all: run logs status

build:
	podman build -t $(IMAGE) .

run: build
	podman run --rm --detach --privileged \
		--name $(CONTAINER) \
		--volume $(TEST_DIR):/tests \
		--volume /sys/fs/cgroup:/sys/fs/cgroup:ro \
		$(IMAGE)

status:
	podman exec -it $(CONTAINER) /usr/bin/systemctl status $(TEST_UNIT)

logs:
	podman exec -it $(CONTAINER) /usr/bin/journalctl -u $(TEST_UNIT)

clean:
	podman rm -f $(CONTAINER) 2>/dev/null || true

shell:
	podman exec -it $(CONTAINER) /usr/bin/bash
