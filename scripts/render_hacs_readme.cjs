/*
Render a README the way HACS shows it, to check it before a release.

Classic HACS (2.0.5) renders the README with the Home Assistant frontend it
bundles (January 2025): it rewrites links like HACS's src/tools/markdown.ts,
renders with marked 15.0.4 (gfm) and sanitises with xss 1.0.15 and the
whitelist of src/resources/markdown-worker.ts. Tags outside the whitelist are
escaped, so they show as text. With --marketplace, <picture> and <source> are
allowed as in the Marketplace of Home Assistant 2026.11 (frontend 20261028.0);
the rest stays as in classic HACS, so treat that mode as an approximation.

Both read the README of the latest release (or the installed version), so pass
that tag as --ref to see the links as HACS makes them.

    npm install --prefix /tmp/hacs-render marked@15.0.4 xss@1.0.15
    NODE_PATH=/tmp/hacs-render/node_modules node scripts/render_hacs_readme.cjs README.md
    NODE_PATH=... node scripts/render_hacs_readme.cjs --marketplace --ref v0.2.0 README.md

Prints the HTML; any "&lt;" in it is a tag that HACS shows as text.
*/

const fs = require("node:fs");
const { marked } = require("marked");
const { filterXSS, getDefaultWhiteList } = require("xss");

const args = process.argv.slice(2);
const option = (name) => {
  const i = args.indexOf(name);
  return i === -1 ? undefined : args.splice(i, 2)[1];
};
const marketplace = args.includes("--marketplace");
const ref = option("--ref") ?? "main";
const repository = option("--repository") ?? "nicohabets/glowcast";
const file = args.filter((a) => a !== "--marketplace")[0] ?? "README.md";

// Port of markdownWithRepositoryContext in hacs/frontend src/tools/markdown.ts
const showGitHubWeb = (text) =>
  text.toLowerCase().includes(".md") || text.toLowerCase().includes(".markdown");

const withRepositoryContext = (input) => {
  input = input.replace(
    /https:\/\/github\.com\/([^\/]+)\/([^\/]+)\/blob\/([^\s]+)/g,
    (x, owner, repo, path) =>
      showGitHubWeb(x) ? x : `https://raw.githubusercontent.com/${owner}/${repo}/${path}`,
  );
  input = input.replace(/\[.*?\]\([^#](?!.*?:\/\/).*?\)/g, (x) => {
    const web = showGitHubWeb(x);
    return x
      .replace("(/", "(")
      .replace(
        "(",
        `(${web ? "https://github.com" : "https://raw.githubusercontent.com"}/${repository}${
          web ? "/blob" : ""
        }/${ref}/`,
      );
  });
  input = input.replace(/\[.*\]\(\#.*\)/g, (x) => x.replace("(#", "(/hacs/repository/0#"));
  input = input.replace(/(?:\w[\w-.]+\/\w[\w-.]+|\B)#[1-9]\d*\b/g, (reference) => {
    const [fullName, issue] = reference.replace(/^#/, `${repository}#`).split("#");
    return `[${reference}](https://github.com/${fullName}/issues/${issue})`;
  });
  return input;
};

const whiteList = {
  ...getDefaultWhiteList(),
  input: ["type", "disabled", "checked"],
  "ha-icon": ["icon"],
  "ha-svg-icon": ["path"],
  "ha-alert": ["alert-type", "title"],
  "ha-qr-code": ["data", "scale", "width", "margin", "error-correction-level", "center-image"],
  ...(marketplace ? { picture: [], source: ["src", "srcset", "sizes", "media", "type"] } : {}),
};

const onTagAttr = (tag, name, value) => {
  if (tag === "input") {
    return (name === "type" && value === "checkbox") || name === "checked" || name === "disabled"
      ? undefined
      : "";
  }
  return undefined;
};

const readme = fs.readFileSync(file, "utf8").replace(/<svg/g, "<disabled").replace(/<\/svg/g, "</disabled");
const html = marked(withRepositoryContext(readme), { breaks: false, gfm: true, async: false });
process.stdout.write(filterXSS(html, { whiteList, onTagAttr }));
