#!/usr/bin/env node
/**
 * Set up the private admin area's credentials.
 *
 *     make vault-password
 *     node scripts/vault-hash-password.mjs
 *
 * Asks for a password (hidden, typed twice), then prints two lines to paste into the root
 * `.env`:
 *
 *     VAULT_PASSWORD_HASH=scrypt:...
 *     VAULT_SESSION_SECRET=...
 *
 * The password itself is never printed, logged or written anywhere — only its hash. This
 * script writes no files and does not touch `.env`; it would be a strange thing for a
 * password tool to edit the file that holds every other secret. You paste, then restart the
 * web app.
 *
 * The hash format and cost parameters must match `apps/web/src/lib/vault/password.ts`. A
 * test (`src/lib/vault/script-compat.test.ts`) hashes here and verifies there, so the two
 * cannot drift apart unnoticed.
 */

import { randomBytes, scrypt } from "node:crypto";
import { fileURLToPath } from "node:url";

const N = 32768;
const R = 8;
const P = 1;
const KEY_LENGTH = 32;
const MIN_LENGTH = 12;

const NL = "\n";
const CR = "\r";
const CTRL_C = String.fromCharCode(3);
const BACKSPACE = String.fromCharCode(8);
const DELETE = String.fromCharCode(127);

export function hashPassword(password) {
  return new Promise((resolve, reject) => {
    const salt = randomBytes(16);
    scrypt(
      password.normalize("NFKC"),
      salt,
      KEY_LENGTH,
      { N, r: R, p: P, maxmem: 128 * N * R * 2 },
      (err, key) =>
        err
          ? reject(err)
          : resolve(["scrypt", N, R, P, salt.toString("base64url"), key.toString("base64url")].join(":")),
    );
  });
}

/** Text already read from a piped stdin but not yet consumed. Both answers can arrive in one
 *  chunk; without carrying the remainder over, the second prompt would wait forever for input
 *  that had already been read and thrown away. */
let piped = "";
let pipedEnded = false;

function readPipedLine() {
  return new Promise((resolve) => {
    const { stdin } = process;

    const take = () => {
      const newline = piped.indexOf(NL);
      if (newline === -1 && !pipedEnded) return false;
      const line = newline === -1 ? piped : piped.slice(0, newline);
      piped = newline === -1 ? "" : piped.slice(newline + 1);
      resolve(line.endsWith(CR) ? line.slice(0, -1) : line);
      return true;
    };

    if (take()) return;

    const onData = (chunk) => {
      piped += chunk;
      if (take()) stdin.off("data", onData);
    };
    stdin.setEncoding("utf8");
    stdin.on("data", onData);
    stdin.once("end", () => {
      pipedEnded = true;
      stdin.off("data", onData);
      take();
    });
    stdin.resume();
  });
}

/** Read a line without echoing it. Falls back to a plain stdin read when not a terminal, so
 *  it can be scripted: printf the password twice, newline-separated, into this script. */
async function readHidden(prompt) {
  const { stdin, stdout } = process;
  stdout.write(prompt);

  if (!stdin.isTTY) {
    const line = await readPipedLine();
    stdout.write(NL);
    return line;
  }

  return new Promise((resolve) => {
    let value = "";
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding("utf8");
    const onKey = (key) => {
      for (const ch of key) {
        if (ch === CR || ch === NL) {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.off("data", onKey);
          stdout.write(NL);
          return resolve(value);
        }
        if (ch === CTRL_C) process.exit(130);
        if (ch === DELETE || ch === BACKSPACE) value = value.slice(0, -1);
        else value += ch;
      }
    };
    stdin.on("data", onKey);
  });
}

async function main() {
  const first = await readHidden("New admin password: ");
  if (first.length < MIN_LENGTH) {
    console.error(`Too short: use at least ${MIN_LENGTH} characters. A long passphrase beats a clever short one.`);
    process.exit(1);
  }
  const second = await readHidden("Type it again:      ");
  if (first !== second) {
    console.error("They do not match. Nothing was generated.");
    process.exit(1);
  }

  const hash = await hashPassword(first);
  const secret = randomBytes(48).toString("base64url");

  console.log(`${NL}Paste these two lines into the root .env, then restart the web app:${NL}`);
  console.log(`VAULT_PASSWORD_HASH=${hash}`);
  console.log(`VAULT_SESSION_SECRET=${secret}`);
  console.log(`${NL}Also set VAULT_ADMIN_EMAILS to the address allowed to sign in (see docs/ENV.md).`);
  console.log("Running this again changes both values, which signs the admin out everywhere.");
}

// Importing this file (the compatibility test does) must not start a prompt.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main();
}
