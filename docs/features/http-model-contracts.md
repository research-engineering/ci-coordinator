# HTTP Model Contracts
Status: accepted design
Last updated: 2026-08-30
Owner requirement: `REQ-CI-RUNTIME-029`

## 1. Decision

The HTTP boundary owns two directional policies and one bounded response role:

- `RequestModel` admits untrusted wire objects by one canonical input spelling;
- `ResponseModel` projects trusted application values to one canonical JSON
  spelling; and
- `ProjectedResponseModel` additionally admits attributes only for the closed
  inventory of DTOs that project trusted immutable application records.

Concrete DTOs own field constraints and cross-field invariants. They do not own
repeated alias, coercion, mutation, or serialization defaults.

## 2. Context And Observables

The decision preserves these public observables:

1. existing request keys remain lower camel case where Python names differ;
2. existing response keys remain unchanged, including the historical
   `unavailable_dependencies` readiness key;
3. every JSON response is serializable without Python-only values;
4. generated OpenAPI and TypeScript remain reproducible from the runtime graph;
   recursive values use a documented conservative static projection; and
5. browser runtime schemas restore exact recursive bounds before semantic use,
   while transport DTOs remain outside domain ownership.

The hard constraints are fail-closed request admission, no undocumented scalar
coercion, finite public JSON numbers, deterministic aliases, exact output keys,
typed constructors, and consumer-schema parity.

The sole admitted transport conversion is the existing JSON string to
`datetime` conversion for `ForceFullCIOverrideRequest.expiresAt`. Its canonical
extended ISO-8601 grammar is
`YYYY-MM-DDTHH:MM:SS[.ffffff][Z|+HH:MM|-HH:MM]`, where the fraction has one to
six digits and the offset is optional. The boundary requires the exact JSON
string type and grammar before `datetime.fromisoformat`; the domain still owns
timezone-awareness and temporal policy. Every integer, Boolean, and string
field remains strict.

## 3. Formal Contract

For request model `R`, response model `S`, Python field name `n`, wire key `w`,
and value `v`:

```text
RequestAccepted(R, input)
=> keys(input) use only each field's admitted validation name
and ExtraKeys(input) = empty
and NoUndeclaredCoercion(input)
ResponseConstructed(S, input)
=> keys(input) use only Python field names
and ExtraKeys(input) = empty
and NoUndeclaredCoercion(input)
ProjectedResponseConstructed(S, input)
=> input belongs to an owner-admitted trusted projection source
and attribute reads are explicitly enabled for S
Emit(S, v) := S(v).to_wire_mapping()
Emit(S, v)
=> JSONValue(result)
and keys(result) use only admitted serialization names
and every finite-number constraint holds
```

Alias directions are deliberately asymmetric:

```text
Request:  lowerCamel wire input -> snake_case Python state
Plan request only: snake_case Python state -> lowerCamel parser input
Response: snake_case/attribute input -> snake_case Python state -> lowerCamel output
```

Request validation aliases are explicit because they define accepted untrusted
input and constructor typing. Serialization aliases exist only on
`PlanRequestBody`, whose admitted parser path serializes and reparses the DTO.
Response aliases follow one serialization-only generator
because the complete current field inventory proves the convention: all 258
predecessor aliases equal `to_camel(field_name)`, there are no per-model
collisions, and only readiness has an output-name exception. Two response
discriminator fields retain the same explicit alias because FastAPI derives
OpenAPI `discriminator.propertyName` from that metadata rather than from the
serialization alias. `validate_by_alias=False` keeps those aliases outside the
response construction language.

## 4. Pydantic Selection

| Candidate                               | Runtime validation    | FastAPI/OpenAPI fit | Cross-field invariants               | Added glue |
|-----------------------------------------|-----------------------|---------------------|--------------------------------------|------------|
| Pydantic `BaseModel` boundary DTOs      | Native                | Native              | Native                               | Lowest     |
| Pydantic `TypeAdapter` plus `TypedDict` | Native                | Partial             | Separate wrapper                     | Higher     |
| Pydantic dataclasses                    | Native                | Supported           | Less direct for this named DTO graph | Higher     |
| Standard-library/manual parsing         | Owner-built           | Owner-built         | Owner-built                          | Highest    |
| Additional serialization library        | Duplicated dependency | Adapter required    | Library-specific                     | Higher     |

Let the hard-feasibility predicate be:

```text
Feasible(c) :=
  StrictRuntimeAdmission(c)
  and ExactNamedSchemas(c)
  and FastAPIIntegration(c)
  and CrossFieldValidation(c)
  and StaticTyping(c)
  and NoDomainContamination(c)
```

`BaseModel` DTOs satisfy every term with the already admitted dependency and
the least additional mechanism. No compared candidate is strictly better on
all hard constraints and maintenance cost. Pydantic is therefore `SELECTED`
for this HTTP representation boundary; it is not required for domain models,
settings, arbitrary collections, persistence records, or canonical signed
bytes.

## 5. Policy Split

`RequestModel` fixes:

- `extra="forbid"` and model-wide strict validation;
- explicit `validate_by_alias=True`, `validate_by_name=False`, and
  `serialize_by_alias=True`;
- validated defaults, finite floats, shallow assignment prevention, and
  redacted validation rendering; and
- explicit validation aliases on every field whose wire name differs, with
  serialization aliases only on the consumed plan-request projection.

`ResponseModel` fixes:

- strict construction from Python names only;
- serialization-only lower-camel aliases with a proved exception mechanism;
- shallow frozen instances and nested model revalidation without claiming deep
  immutability;
- validated defaults, finite floats, and serialization-schema requiredness;
  and
- one final JSON-mode mapping operation.

`ProjectedResponseModel` adds only `from_attributes=True`. Its exhaustive class
inventory is enforced because attribute access can execute descriptors and is
therefore a capability, not a harmless parsing convenience. Mapping-based
wrappers and error DTOs must remain plain `ResponseModel` subclasses.

The response generator is not used for request validation. This prevents a
dynamic input alias from weakening static constructor analysis. The Pydantic
mypy plugin rejects future required dynamic request aliases.

## 6. Alternatives Rejected

- **Per-module `ConfigDict`:** a new DTO could silently restore lax coercion,
  dual-name input, Python-mode dumping, or non-finite numbers.
- **Explicit aliases on every response field:** correct but repeats one proved
  naming function and turns policy changes into unrelated field edits.
- **Generated request aliases:** imprecise in typed constructor signatures when
  name input is disabled; explicit aliases retain wire authority and typing.
- **Deep-frozen JSON:** adds allocation and identity semantics that no current
  response lifecycle requires.

## 7. Enforcement And Falsifiers

The requirement is falsified if any of these states passes assigned CI:

- a concrete HTTP DTO derives directly from `BaseModel`;
- an unowned, aliased, or module-form Pydantic import bypasses the closed
  boundary vocabulary;
- an admitted Pydantic policy callable escapes a direct call or is shadowed;
- `populate_by_name`, an uninventoried `alias=`, or local model policy returns;
- a request accepts both field name and alias;
- a response accepts a wire alias as construction input;
- a non-projection response reads object attributes, or an uninventoried DTO
  enables attribute projection;
- a string is coerced to an integer or Boolean;
- `NaN` or infinity reaches a public response model;
- two accepted or emitted names collide;
- a response dump omits explicit JSON mode;
- an emitted default is absent from serialization-schema requiredness; or
- generated OpenAPI or TypeScript differs from the admitted runtime projection.

The `alias=` prohibition has one exhaustive exception inventory: the two
profile-capacity discriminator fields required to project the existing
`executionKind` OpenAPI discriminator. Any third declaration is rejected.

Enforcement is split across the Pydantic mypy plugin, an exhaustive HTTP model
policy witness, route tests, generated-contract checks, lint, type checking,
and the Proofkit requirement binding. A finite mutation inventory separately
proves that CI kills the named request-name, scalar-coercion, response-name,
attribute-projection, and output-alias policy relaxations; it makes no claim of
open-world mutation completeness.

## 8. Non-Claims And Revision Conditions

This design does not prove domain authorization, resource bounds before body
parsing, canonical signing bytes, provider behavior, deployment, or production
readiness. Pydantic validation does not replace route-specific admission.

Revisit the decision if FastAPI adopts a different native schema/validation
contract, measured model cost violates an admitted latency budget, request
aliases require versioned dual-read migration, or response consumers require a
naming convention not expressible by one injective projection.
