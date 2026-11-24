// The rowkeep the tests run: the script the skill ships.
// ROWKEEP_PYTHON names the Python to run it with, if not the usual one.
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SKILL = path.join(root, "plugin", "skills", "rowkeep");
export const REG = path.join(SKILL, "registry", "verbs.json");
// ROWKEEP_SCRIPT names another copy of the script to test, such as an older version's.
export const SCRIPT = process.env.ROWKEEP_SCRIPT || path.join(SKILL, "scripts", "rowkeep.py");
export const PYTHON = process.env.ROWKEEP_PYTHON || (process.platform === "win32" ? "python" : "python3");

// The program, and the arguments that come before rowkeep's own.
export const BIN = PYTHON;
export const PRE = [SCRIPT];
