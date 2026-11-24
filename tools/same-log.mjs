// node tools/same-log.mjs DIR_A DIR_B
// Compare two exports of a log, document by document: every file under stream/ and ix/ in
// each folder (as saved with out_dir). Used to prove a restore: the restored log must hold
// exactly the documents the original held. Key order and number formatting are ignored.
import fs from "node:fs";
import path from "node:path";

const [a, b] = process.argv.slice(2);
if (!a || !b) { console.log("usage: node tools/same-log.mjs DIR_A DIR_B"); process.exit(2); }
const canon = (x) => Array.isArray(x) ? "[" + x.map(canon).join(",") + "]"
  : x && typeof x === "object" ? "{" + Object.keys(x).sort().map((k) => JSON.stringify(k) + ":" + canon(x[k])).join(",") + "}" : JSON.stringify(x);
const load = (dir, sub) => {
  const d = path.join(dir, sub), out = new Map();
  if (fs.existsSync(d)) for (const f of fs.readdirSync(d)) if (f.endsWith(".json")) out.set(f, canon(JSON.parse(fs.readFileSync(path.join(d, f), "utf8"))));
  return out;
};
let bad = 0;
for (const sub of ["stream", "ix"]) {
  const x = load(a, sub), y = load(b, sub);
  let same = 0;
  for (const [f, v] of x) {
    if (!y.has(f)) { bad++; console.log(`DIFFERENT ${sub}/${f}: only in the first`); }
    else if (y.get(f) !== v) { bad++; console.log(`DIFFERENT ${sub}/${f}: contents differ`); }
    else same++;
  }
  for (const f of y.keys()) if (!x.has(f)) { bad++; console.log(`DIFFERENT ${sub}/${f}: only in the second`); }
  console.log(`${sub}: ${x.size} documents in the first, ${y.size} in the second, ${same} identical`);
}
console.log(bad ? `${bad} differences` : "the two logs are identical");
process.exit(bad ? 1 : 0);
