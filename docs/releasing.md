# Releasing

Developer notes on what happens around a GitHub release. The steps of a release itself are in its issue (for example #165).

## The release zip for HACS

`hacs.json` has `"zip_release": true` and `"filename": "glowcast.zip"` (#189): HACS installs and updates Glowcast from the release asset `glowcast.zip`, not from the source of the tag, and unpacks it into `custom_components/glowcast/`. So the zip holds the contents of that folder at its root (`manifest.json`, `__init__.py`, `brand/`, ...).

- Publishing a release runs the workflow `.github/workflows/release-zip.yml`, which builds the zip with `scripts/build_release_zip.py` from the release's tag and attaches it. Check that the release shows `glowcast.zip` under "Assets" before announcing it: without the asset HACS cannot install that release.
- Publish the release by hand (or with a personal token): a release created by a workflow with its `GITHUB_TOKEN` does not start other workflows, so it would get no zip.
- For an existing tag, run the workflow by hand; it replaces an existing `glowcast.zip`. Every release HACS may install needs the zip, older ones too (a user can pick an older version in HACS):

  ```bash
  gh workflow run release-zip.yml --repo nicohabets/glowcast -f tag=v0.2.0
  ```

- Build and list the zip locally: `python3 scripts/build_release_zip.py /tmp/glowcast.zip`.

## Reading the download counts

GitHub counts the downloads of every release asset. HACS downloads `glowcast.zip` on every install, update and reinstall, so the count is an estimate of use, not a count of users. Downloads of the source code (tarball and zip) are not counted.

```bash
gh api repos/nicohabets/glowcast/releases --jq '.[] | {tag: .tag_name, downloads: [.assets[].download_count]}'
```
