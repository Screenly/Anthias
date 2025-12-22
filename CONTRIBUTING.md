# Contributing

## :seedling: First Steps

Make sure that you have [Git](https://git-scm.com/) installed on your machine.
You can install the [GitHub CLI](https://cli.github.com/) to make it easier to fork and clone repositories and checking out between pull requests.

To get started, fork the [Screenly/Anthias](https://github.com/Screenly/Anthias/) repository and clone your fork.

```bash
git clone https://github.com/your-username/Anthias.git
cd Anthias
```

## :lady_beetle: Creating Issues

When creating an issue, you'll be prompted to select one of the following
types:

- Bug Report
- Dependency Upgrade
- Feature Request

## :bulb: Pull Requests

### Creating Pull Requests

- All pull requests should be made against the `master` branch of the
  [Screenly/Anthias](https://github.com/Screenly/Anthias/) repository.
- Associate the pull request with the [Anthias project](https://github.com/orgs/Screenly/projects/2).
- Add a label to the pull request that describes the changes you made.
  - Add a `bug` label if you are fixing a bug.
  - Add an `enhancement` label if you are adding a new feature or modifying
    existing functionality.
  - Add a `documentation` label if you are updating the documentation.
  - Add a `chore` label if you are doing tasks that don't alter Anthias'
    functionality.
  - Add a `webview` label if you are making changes to the [WebView](/webview/README.md).
  - Add a `tests` label if you are adding or modifying unit or integration tests.
- Make sure that all of the items in the [checklist](.github/pull_request_template.md) are checked before having it reviewed and merged.
- Don't forget to assign reviewers to your pull request.

### Merging Pull Requests

- All items in the [checklist](.github/pull_request_template.md) should be satisfied before merging.
- For pull requests with more than 5 commits, squash the commits before merging.
