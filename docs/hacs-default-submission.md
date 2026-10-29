# Submitting Glowcast to the default HACS list

How to get Glowcast into the default HACS list, which is also the catalog of Home Assistant's Marketplace (2026.11+). Prepared in #48 from the HACS documentation and the `hacs/default` repository as of October 2026. The repository owner submits; HACS reviews.

Sources: [hacs.xyz/docs/publish/include](https://hacs.xyz/docs/publish/include), [the HACS action](https://hacs.xyz/docs/publish/action), [Marketplace: get into the catalog](https://contributors.home-assistant.io/marketplace/guides/catalog/), and the workflows and PR template of [hacs/default](https://github.com/hacs/default).

## What HACS checks, and when

| Check | Run against |
|---|---|
| HACS action, no ignored checks | the **latest release** |
| hassfest | the **default branch** (`main`) |
| releases | at least one full GitHub release (a tag alone does not count) |
| owner | the PR author owns the repository |
| editable | "Allow edits by maintainers" on, so not from an organisation fork |
| core domain conflict | `glowcast` is not a core domain, also not in the next release |
| lint | the `integration` file is valid JSON and sorted case-insensitively |

So the README, the brand images and `hacs.json` that the reviewers see are the ones in the latest release. That is why the release comes first.

## Steps, in order

1. **Merge the PR for #48**, so `main` runs the HACS action without `ignore`.
2. **Make a new release from `main`** (version: see the question on #48; v0.1.1 is proposed). As with v0.1.0: bump `version` in `custom_components/glowcast/manifest.json` to match the tag, add release notes under `docs/releases/`, and publish a full GitHub release, not a pre-release.
3. **Wait for CI on `main` to be green** after the release commit, and note the links of:
   - the release: `https://github.com/nicohabets/glowcast/releases/tag/<tag>`
   - the CI run on `main` (`https://github.com/nicohabets/glowcast/actions/runs/<id>`), with its **HACS** job (`.../job/<id>`) and its **hassfest** job.
   The HACS job must show `category: integration` and no `ignore` in its "Run hacs/action@main" step.
4. **Fork [hacs/default](https://github.com/hacs/default)** under your personal account, and create a new branch from `master` (for example `add-glowcast`). Do not work on the fork's `master`: the bot closes PRs from `master`.
5. **Add one line to the file `integration`**, in alphabetical order (case-insensitive), and change nothing else:

   ```json
     "NickM-27/swatch-hass-integration",
     "nicohabets/glowcast",
     "NicoIIT/ha-ble-adv",
   ```

   The neighbours were checked on 2026-10-29; if they changed, keep the order of `sorted(..., key=str.casefold)`. The name must match GitHub's spelling exactly: `nicohabets/glowcast`. Keep the 2-space indentation and the newline at the end of the file.
6. **Open the PR** to `hacs/default` `master` with the template below, every box ticked and all three links filled in. Leave "Allow edits by maintainers" on. **Do not request reviews**; the template says that gets the PR closed.
7. **Wait.** A bot adds the label `New default repository` and runs the checks. If it does not add the label, the checks do not run; closing and reopening the PR started them in other submissions. A reviewer then approves, asks for changes, or drafts the PR. After changes: push a new release, then re-request the review.
8. **After the merge**, Glowcast is in the next scheduled scan (the catalog is rebuilt every few hours). Then search for "Glowcast" in HACS and in the Marketplace and check that it installs like the custom repository (scenario "listed" of #48). Reviewers often ask for the README's install section to start with searching in HACS; that change is then a small follow-up.

## The PR to hacs/default

Title: `Add nicohabets/glowcast`

```markdown
<!--
DO NOT REQUEST REVIEWS, THAT IS JUST RUDE, IF YOU DO THE PULL REQUEST WILL BE CLOSED!
Make sure to check out the guide here: https://hacs.xyz/docs/publish/start
-->
## Checklist

<!-- Do not open a pull request before you have completed all these, it will be closed. -->

- [x] I've read the [publishing documentation](https://hacs.xyz/docs/publish/start).
- [x] I've added the [HACS action](https://hacs.xyz/docs/publish/action) to my repository.
- [x] (For integrations only) I've added the [hassfest action](https://developers.home-assistant.io/blog/2020/04/16/hassfest/) to my repository.
- [x] The actions are passing without any disabled checks in my repository.
- [x] I've added a link to the action run on my repository below in the links section.
- [x] I've created a new release of the repository after the validation actions were run successfully.

## Links

<!-- Do not open a pull request before you have provided all these, it will be closed. -->

Link to current release: https://github.com/nicohabets/glowcast/releases/tag/<tag>
Link to successful HACS action (without the `ignore` key): https://github.com/nicohabets/glowcast/actions/runs/<run id>/job/<HACS job id>
Link to successful hassfest action (if integration): https://github.com/nicohabets/glowcast/actions/runs/<run id>/job/<hassfest job id>

<!-- tid:73253df5-5376-4e68-8c16-b234da6a2de3 -->
```

Keep the last comment line (`tid:...`): it is part of HACS's template. Fill in the three links from step 3.

## Common reasons a submission is closed or sent back

Seen in recent `hacs/default` PRs (October 2026), with Glowcast's status:

| Reason | Glowcast |
|---|---|
| Checklist not ticked, or fewer than 3 links | Use the template above in full |
| PR from the fork's `master`, several repositories in one PR, wrong letter case of the name | Steps 4 to 6 |
| No `brand/icon.png` in the latest release | In v0.1.0 and later (#44) |
| No `translations/en.json` | Present, with `nl.json` |
| `categories` in the manifest | Not used: hassfest rejects it for custom integrations at the moment, although the HACS action warns that it is missing |
| Blocking I/O in the event loop, secrets in logs, a `ConfigEntryAuthFailed` without a reauth step | Covered by the code rules in `CLAUDE.md`; a reauth step exists |
| Duplicating or overriding a core integration | No: `glowcast` is its own domain |
| Features not tested on real hardware | Each feature is checked on real hardware before merging |
