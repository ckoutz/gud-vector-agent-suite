import { randomBytes } from "node:crypto";

export function createId(prefix: string) {
  return `${prefix}_${randomBytes(12).toString("hex")}`;
}

export function randomPassword() {
  return randomBytes(9).toString("base64url");
}

export function createClaimToken() {
  return randomBytes(24).toString("base64url");
}
