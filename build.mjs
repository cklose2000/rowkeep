// node build.mjs [--label "Test Log"]
//
// Builds the two pages a Rowkeep account has:
//   dist/rowkeep.html        the log's viewer, with its two scripts inlined
//   dist/rowkeep-vault.html  the vault's page
// With --label, also writes a relabelled pair (dist/rowkeep-test-log.html and
// dist/rowkeep-test-log-vault.html for the label above), titled "Rowkeep Test Log" and
// "Rowkeep Test Log Vault". Setup finds a log by the exact title "Rowkeep", so a throwaway
// copy used for testing must not carry that title.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const read = (f) => fs.readFileSync(path.join(root, "src", f), "utf8");

let log = read("as-page.html");
for (const [mark, file] of [["/*__STORE__*/", "as-store.js"], ["/*__UI__*/", "as-ui.js"]]) {
  const js = read(file);
  if (js.includes("</script")) throw new Error(file + " contains a closing script tag");
  if (!log.includes(mark)) throw new Error("missing marker " + mark);
  log = log.replace(mark, () => "\n" + js);
}
const vault = read("vault-page.html");

const out = path.join(root, "dist");
fs.mkdirSync(out, { recursive: true });
fs.writeFileSync(path.join(out, "rowkeep.html"), log);
fs.writeFileSync(path.join(out, "rowkeep-vault.html"), vault);
const written = ["dist/rowkeep.html", "dist/rowkeep-vault.html"];

const at = process.argv.indexOf("--label");
if (at > 0) {
  const label = (process.argv[at + 1] || "").trim();
  if (!/^[A-Za-z0-9 ]{1,30}$/.test(label)) throw new Error("--label takes a short name of letters, digits and spaces");
  const swap = (html, pairs) => {
    for (const [a, b] of pairs) {
      if (!html.includes(a)) throw new Error("text to relabel not found: " + a);
      html = html.replace(a, () => b);
    }
    return html;
  };
  const slug = label.toLowerCase().replace(/ +/g, "-");
  fs.writeFileSync(path.join(out, `rowkeep-${slug}.html`), swap(log, [["<title>Rowkeep</title>", `<title>Rowkeep ${label}</title>`], ["<h1>Rowkeep</h1>", `<h1>Rowkeep: ${label}</h1>`]]));
  fs.writeFileSync(path.join(out, `rowkeep-${slug}-vault.html`), swap(vault, [["<title>Rowkeep Vault</title>", `<title>Rowkeep ${label} Vault</title>`], ["<h1>Rowkeep vault</h1>", `<h1>Rowkeep vault: ${label}</h1>`]]));
  written.push(`dist/rowkeep-${slug}.html`, `dist/rowkeep-${slug}-vault.html`);
}
console.log(written.join(", ") + " (" + (log.length / 1024).toFixed(1) + " KiB for the log's page)");
