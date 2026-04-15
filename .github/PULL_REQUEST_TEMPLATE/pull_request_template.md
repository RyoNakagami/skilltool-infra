# Pull Request Template

## Summary

- Briefly describe what this PR does and why.

## Related Issue

- Closes / References: #IssueNumber

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring / Code cleanup
- [ ] Documentation update
- [ ] Other:

## Description

- Detailed explanation of the changes.
- Reference any related issues or tickets.
- Mention any new modules, classes, or functions added.

## Changes Made

- **New Modules / Classes**: e.g., `exceptions.py` for dddd.
- **Moved / Refactored**: e.g., `BalancedPanelError` moved from `general.py` to `exceptions.py`.
- **Enhanced / Fixed**: e.g., added type hints and error handling in `CheckRawData`.
- **Removed / Deprecated**: e.g., `create_pokemon` in `foofoo.py`.

## Examples

- Provide examples or instructions to test the changes.
- Include code snippets if applicable.

```python
# Example snippet
from exceptions import BalancedPanelError
```

## Additional Notes

- Any additional comments, warnings, or context.

## Tests

### Unit Tests

- [ ] New unit tests added for added functionality
- [ ] Existing unit tests updated for modified behavior
- [ ] All unit tests pass locally

### Continuous Integration (CI)

- [ ] CI pipeline triggered
- [ ] CI checks passed (linting, formatting, tests, coverage)

### Manual Testing (if applicable)

- [ ] Steps to reproduce manual test cases
- [ ] Observed results match expected results

## Checklist

- [ ] Code follows Python style guidelines
- [ ] Docstrings added or updated where necessary
- [ ] Type hints added/updated
- [ ] Logging and error handling verified
- [ ] Dependencies updated if needed
- [ ] Breaking changes documented
- [ ] Related issue referenced in this PR
