import { expect, test } from "vitest";
import { proxyRequestIsAdmitted } from "../src/api/development/proxyPolicy";

const keycloak = "/api/v1/auth/keycloak/callback";
const github = "/api/v1/repository-attestations/github/callback";
const state = "s".repeat(43);
function query(fields: Record<string, string>) {
  return new URLSearchParams(fields).toString();
}
const valid = { code: "one", state, iss: "https://identity.example/realms/operators" };

test.each([1, 1024])("Keycloak forwards %i Unicode codepoints with a required issuer", (length) => {
  expect(
    proxyRequestIsAdmitted(
      "GET",
      `${keycloak}?${query({ ...valid, code: "\u{1f642}".repeat(length) })}`,
    ),
  ).toBe(true);
});
test.each([1, 2048])(
  "Keycloak forwards an issuer of %i codepoints; equality is backend-owned",
  (length) => {
    expect(
      proxyRequestIsAdmitted(
        "GET",
        `${keycloak}?${query({ ...valid, iss: "\u{1f642}".repeat(length) })}`,
      ),
    ).toBe(true);
  },
);
test.each([undefined, "!", "~".repeat(512)])(
  "Keycloak admits optional bounded session_state: %s",
  (sessionState) => {
    expect(
      proxyRequestIsAdmitted(
        "GET",
        keycloak +
          "?" +
          query({
            ...valid,
            ...(sessionState === undefined ? {} : { session_state: sessionState }),
          }),
      ),
    ).toBe(true);
  },
);
test.each([
  { ...valid, code: "" },
  { ...valid, code: "x".repeat(1025) },
  { ...valid, iss: "" },
  { ...valid, iss: "x".repeat(2049) },
  { ...valid, state: "s".repeat(42) },
  { ...valid, state: `${state}\n` },
  { ...valid, session_state: "" },
  { ...valid, session_state: "x".repeat(513) },
  { ...valid, session_state: " " },
  { ...valid, session_state: "\u007f" },
  { ...valid, session_state: "\u00e9" },
  { ...valid, unknown: "x" },
])("Keycloak rejects the exact invalid structural boundary %j", (fields) => {
  expect(proxyRequestIsAdmitted("GET", `${keycloak}?${query(fields)}`)).toBe(false);
});
test.each(["code", "state", "iss", "session_state"])(
  "duplicate %s including encoded aliases is not forwarded",
  (field) => {
    const fields = { ...valid, session_state: "one" };
    const encoded = `%${field.charCodeAt(0).toString(16)}${field.slice(1)}`;
    for (const name of [field, encoded]) {
      expect(proxyRequestIsAdmitted("GET", `${keycloak}?${query(fields)}&${name}=two`)).toBe(false);
    }
  },
);
test.each(["code", "state", "iss"])("Keycloak requires %s", (field) => {
  const fields = new URLSearchParams(valid);
  fields.delete(field);
  expect(proxyRequestIsAdmitted("GET", `${keycloak}?${fields}`)).toBe(false);
});
test.each([1, 512])("GitHub retains its ASCII %i-byte code contract", (length) => {
  expect(
    proxyRequestIsAdmitted("GET", `${github}?${query({ code: "x".repeat(length), state })}`),
  ).toBe(true);
});
test.each([
  { code: "", state },
  { code: "x".repeat(513), state },
  { code: "\u00e9", state },
  { code: "one", state: `${state}\n` },
  { code: "one", state, iss: valid.iss },
  { code: "one", state, session_state: "x" },
])("GitHub rejects Keycloak-only or invalid fields %j", (fields) => {
  expect(proxyRequestIsAdmitted("GET", `${github}?${query(fields)}`)).toBe(false);
});
test.each(["POST", "PUT", "DELETE", undefined])("callbacks reject method %s", (method) => {
  expect(proxyRequestIsAdmitted(method, `${keycloak}?${query(valid)}`)).toBe(false);
  expect(proxyRequestIsAdmitted(method, `${github}?${query({ code: "one", state })}`)).toBe(false);
});

test.each(["code", "state"])("GitHub still requires exactly one decoded %s field", (field) => {
  const fields = new URLSearchParams({ code: "one", state });
  fields.delete(field);
  expect(proxyRequestIsAdmitted("GET", `${github}?${fields}`)).toBe(false);
  for (const name of [field, `%${field.charCodeAt(0).toString(16)}${field.slice(1)}`]) {
    expect(
      proxyRequestIsAdmitted("GET", `${github}?${query({ code: "one", state })}&${name}=two`),
    ).toBe(false);
  }
});
