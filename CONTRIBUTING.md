# Contributing to snapm

Contributions, whether features or bug fixes, are welcome. We aim to be
responsive and helpful in project issues and feature requests. For
larger changes, consider discussing the change first by filing an issue;
the maintainers can then add to or create a GitHub project to track the
work. This also gives us a chance to provide feedback on the feasibility
and suitability of your proposal, which helps ensure your hard work
aligns with the project's direction.

-----

Following these guidelines will help you get up to speed quickly,
enabling you to develop and test changes for `snapm` and facilitating a
smooth review and merge process. This document provides instructions for
setting up your development environment, our coding style, and how to
run the test suite.

-----

## Writing and Submitting Changes

We use a standard **GitHub pull request workflow**. Here are the basic steps:

1.  **Fork** the repository on GitHub.
2.  **Clone** your fork to your local machine.
3.  Create a **new branch** for your changes.
4.  Make your changes and **commit** them with a clear, concise message.
5.  **Push** your changes to your fork on GitHub.
6.  Create a **pull request** from your fork to the main snapm repository.
7.  A committer will **review** your pull request. You may be asked to
    make changes.
8.  Once your pull request is approved and all tests pass, it will be
    **merged**.

-----

## Setting up a Dev Environment

To get started with development on a **RHEL, Fedora, or CentOS-based
system**, you'll need to install the necessary build and runtime
dependencies.

Install the build dependencies with this command:

```bash
# dnf builddep boom-boot snapm
```

You'll also need `lvm2` and `stratisd` for the test suite to run
correctly.

```bash
# dnf install lvm2 stratisd
```

-----

## Coding Style

To maintain a consistent coding style, we use a few tools to format and
lint our code.

  * **black**: All Python code in the `snapm` package should be
    formatted with `black` (tests are currently excluded from automatic
    formatting).
  * **pylint**: Your code should pass a `pylint` check using the
    `.pylintrc` file in the root of the repository.
  * **pycodestyle/bandit**: We also use `pycodestyle` and `bandit` for
    additional style and security checks. Check out the `snapm.yml`
    GitHub Actions workflow for tips on running these locally.
  * **Sphinx Docstrings**: All functions and methods should have a
    docstring in Sphinx format. You can find a good guide
[here](https://sphinx-rtd-tutorial.readthedocs.io/en/latest/docstrings.html).

-----

## Running Tests

We have a comprehensive test suite to ensure the quality of our code. We
**strongly** recommend running the tests in a virtual machine or other
isolated environment. The test suite creates LVM2 and Stratis devices
using the Linux loopback device and will modify the system state outside
of the source directory.

### Requirements

  * The test suite requires `pytest` (and  optionally `coverage`). You can
    install them with `pip` or `dnf` (On RHEL/Fedora/CentOS systems these
    packages are named `python3-pytest` and `python3-coverage`).
  * You'll need about **\~250MiB of free space in `/var/tmp`** for the
    tests to create temporary files and filesystems.
  * A full test run takes approximately **25-30 minutes**, depending on
    system performance.
  * You'll need to copy the `snapm` configuration files and systemd
    units/tmpfiles.d configuration into the system and notify systemd.
    From the project root directory, run:
    ```bash
    # cp -r etc/snapm /etc
    # cp systemd/*.timer systemd/*.service /usr/lib/systemd/system
    # cp systemd/tmpfiles.d/snapm.conf /usr/lib/tmpfiles.d
    # systemd-tmpfiles --create /usr/lib/tmpfiles.d/snapm.conf
    # systemctl daemon-reload
    ```

### Suggested Commands

To run the entire test suite with coverage checking, use the following
commands:

```bash
# coverage run /usr/bin/pytest -v --log-level=debug tests
# coverage report --include "./snapm/*"
```

To run a specific test, you can use the `-k` flag with `pytest`:

```bash
# pytest -v --log-level=debug tests -k <test_name_pattern>
```

### Container Tests

We also have a set of container-based tests. These tests require the
`podman` package to be installed. You can run them using the `Makefile`
in the `container_tests` directory:

```bash
$ make -C container_tests clean
$ make -C container_tests all
$ make -C container_tests report
```

### Cleaning Up After Failed Tests

The test suite aims to be self-contained and to clean up after itself.
Certain kinds of test failures during development might leave some
artifacts behind. To clean them up run the `tests/bin/cleanup.sh`
script:

```bash
# tests/bin/cleanup.sh
Clean up test suite mounts and devices? (y/n): y
```

To skip the confirmation prompt, use `--force`:

```bash
# tests/bin/cleanup.sh --force
```
