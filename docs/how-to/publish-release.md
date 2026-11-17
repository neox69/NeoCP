# Publish a release

This page is for maintainers.

1. Increase `__version__` in `neocp/__init__.py` and `version` in `pyproject.toml`.
2. Create the version tag:

   ```bash
   git tag vX.Y.Z
   ```

3. Push the tag to GitHub:

   ```bash
   git push origin vX.Y.Z
   ```

The workflow `.github/workflows/release.yml` runs the tests, builds the wheel, and publishes the release. The release contains the macOS and Windows ZIP files, the `.midiremote` file, and the `.js` script.

To build the packages locally:

```bash
python -m build
```

```bash
python scripts/make_release.py
```
