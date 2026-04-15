# Release {{RELEASE_VERSION}}

> This release is the main release reflecting the final adjustments made in beta-release-{{BETA_VERSION}}.
> It does not include new features; it primarily consists of version number updates, release notes,
> minor fixes, and tested corrections.

## 1. Version

- Main release version: {{RELEASE_VERSION}}
- Git tag: v{{RELEASE_VERSION}}

## 2. Summary

> Updated version number (`pyproject.toml`, `__version__.py`, etc.)
> Updated release notes
> Minor fixes to documentation and sample code
> Minor corrections confirmed in final testing

## 3. Changelog

### New Feature

- None in this release (all new features are already completed in the develop branch)

### Enhancement

- None in this release (only minor adjustments from beta-release)

### Fixed

- Stabilized handling of NaN, inf, and negative inputs
- Corrected typos in sample code and documentation
- Fixed minor rounding errors in statistical calculations

### Deprecated

- None

### Breaking Changes

- None

---

## 4. Testing & Validation

- [ ] Unit tests (pytest / unittest) passed
- [ ] Verified statistical calculation results (reproducibility of estimates and test statistics)
- [ ] Checked edge cases (NaN, inf, negative values)
- [ ] Verified operation of documentation and sample code
