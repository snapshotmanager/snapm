
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

Before you start your work we suggest searching for an existing issue that
describes the change you'd like to see. If there is no issue already open
please file one so that the team can review the proposal and provide feedback
and guidance. This applies to both fixes/bugs and new feature development.

We use a standard **GitHub pull request workflow**. Here are the basic steps:

1.  **Fork** the repository on GitHub.
2.  **Clone** your fork to your local machine.
3.  Create a **new branch** for your changes.
4.  Make your changes and **commit** them with a clear, concise message (see
    below for advice on how to format your commit message).
5.  **Push** your changes to your fork on GitHub.
6.  Create a **pull request** from your fork to the main snapm repository.
7.  A committer will **review** your pull request. You may be asked to
    make changes.
8.  Once your pull request is approved and all tests pass, it will be
    **merged**.

### Commit message formatting

We use a consistent format for commit messages and we expect all contributors
to follow this pattern. If your commits are not formatted in the expected way
you may be asked to change or re-write them before your changes can be merged.
You can do this easily using the ``git commit --amend`` or ``git rebase
--interactive`` commands. If you are not familiar with these and you receive a
request to amend a commit please ask—we are always happy to help new
contributors get the hang of the workflow!

The commit subject (first line) should be in the format:

```text
subsystem: description of change
```

Where "subsystem" is free-form text that should succinctly describe the area of
the codebase that your patch touches. There are no really hard rules here, but
we encourage the use of the module name if changing a single Python file (minus
any dot-separated prefix needed to uniquely identify the module to Python—so
"lvm2" rather than "snapm.manager.plugins.lvm2", for example). If your changes
touch several files in the same package you should use the package name (that
would be "plugins" to continue with the previous example). Changes that are
either tree-wide, or that affect several packages (such as a refactoring that
lifts functionality upwards in the package tree) typically use the top-level
"snapm" package as the subsystem. Occasionally a comma-separated list may make
sense: for example "lvm2,stratis" but this easily gets unwieldy so it is not
routinely encouraged.

The remainder of the subject should be a brief description of the change or
what it achieves. Again there are few strict rules but it should be possible
for a reviewer to get a rough idea of what to expect in the patch. We do not
enforce a hard limit on the length of the subject but wherever possible it is
good to keep it to fewer than 80 characters (sometimes this is difficult,
especially when naming particular functions or class names—that's fine, and we
review these on a case-by-case basis).

Some examples of common subsystem strings used in the project are:

  * **snapm**: the top-level "catch all" subsystem
  * **plugins**: changes that affect all plugins, or the plugin package itself.
    Use the plugin module name if your patch only affects one plugin.
  * **doc**: documentation (including all MarkDown files in the root, as well
    as the Sphinx documentation in doc/, and the groff formatted man pages in
    the man/ directory).
  * **tests**: changes to any of the test suites. It is also fine to use the
    specific suite name if that makes sense ("``virt_tests``",
    "``container_tests``", etc).
  * **dist**: package and distribution metadata: RPM spec file, Python project
    and build metadata etc.
  * **scripts**: patches affecting the demo & helper scripts found in the
    scripts/ directory of the source tree.

The commit body (the main text of the commit which follows the subject,
separated by a single empty line) should contain a description of the change if
necessary (often the subject is sufficient, especially for simple changes).

Most changes (even simple fixes or refactors) should be accompanied by an issue
(whether for a fix, or a new feature). Please reference the issue in the commit
using the standard GitHub tags:

```text
Related: #123
Resolves: #232
```

In most cases it's best to have a one-to-one relationship between commits and
issues, but if the problem you are working on is complicated it is perfectly
fine (and encouraged) to break it up into smaller chunks. You can file
sub-issues on GitHub for these if you wish (and it can be helpful when working
on large or complex features/fixes) but it is also acceptable to tag the series
with the "Related:" keyword, and then use "Resolves:" for the final commit that
ends the series.

You are welcome to include tool output, code snippets, and diff output in your
commit message if it provides helpful context. If you do so please indent such
text by at least two characters. This makes the message more readable and
helps to prevent the content being mis-interpreted by programs like
``patch(1)`` which other developers or the maintainers may use to process your
changes.

-----

## Setting up a Dev Environment

To get started with development on a **RHEL, Fedora, or CentOS-based
system**, you'll need to install the necessary build and runtime
dependencies.

Install the build dependencies with this command:

```bash
dnf builddep boom-boot snapm
```

You'll also need `lvm2` and `stratisd` for the test suite to run
correctly.

```bash
dnf install lvm2 stratisd
```

Install ``boom-boot`` first, either from distribution packages or using
the manual installation instructions from the project README.md.

Create a venv to isolate the installation (use ``--system-site-packages``
to use the installed packages, rather than building from source):

```bash
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
```

Install in editable mode:

```bash
git clone https://github.com/snapshotmanager/snapm.git
cd snapm
python3 -m pip install -e .
```

Or run from a git clone:

```bash
git clone https://github.com/snapshotmanager/snapm.git
cd snapm
export PATH="$PWD/bin:$PATH" PYTHONPATH="$PWD:$PYTHONPATH"
snapm <type> <command> ...
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
  * **pycodestyle**: We also use `pycodestyle` for
    additional style and security checks. Check out the `snapm.yml`
    GitHub Actions workflow for tips on running this locally.
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

  * The test suite requires `pytest` (and optionally `coverage`). You can
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
coverage run -m pytest -v --log-level=debug tests
coverage report --include "./snapm/*"
```

To run a specific test, you can use the `-k` flag with `pytest`:

```bash
pytest -v --log-level=debug tests -k <test_name_pattern>
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
tests/bin/cleanup.sh
Clean up test suite mounts and devices? (y/n): y
```

To skip the confirmation prompt, use `--force`:

```bash
tests/bin/cleanup.sh --force
```

## Building the Documentation

We use Sphinx. To build the HTML docs locally:

```bash
python3 -m pip install -r requirements.txt
make -C doc html
xdg-open doc/_build/html/index.html
```
