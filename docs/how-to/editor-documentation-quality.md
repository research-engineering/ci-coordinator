# Configure Documentation Checks In Your Editor

## Shared Vocabulary

The sole accepted-word list is
[`tooling/quality/spelling-words.dic`](../../tooling/quality/spelling-words.dic).
It is UTF-8 plain text: one lowercase word per line, sorted and unique, without
comments, wildcards, affix rules or a Hunspell word-count header. Review every
addition as vocabulary, not as permission to ignore incorrect usage in context.
Do not add hash fragments, partial product names or whole diagnostic messages.

The existing codespell CI command consumes that same file through
`--ignore-words`; its dictionaries and source selection remain unchanged.
The root [`cspell.json`](../../cspell.json) connects CSpell-compatible editors
to the same list. No additional CSpell CLI dependency or second spelling CI
job is required. Different engines can tokenize compound names differently;
a shared word list does not promise identical diagnostics.

## IntelliJ IDEA And Other JetBrains IDEs

1. Open **Settings > Editor > Natural Languages > Spelling**.
2. Add `tooling/quality/spelling-words.dic` as a custom dictionary for this
   project. Do not import the words into the global or project XML dictionary.
3. Remove an older manually copied Coordinator word list, if one exists.
4. Keep the **Spelling**, **Grammar**, **Style**, and **Link with unencrypted
   protocol** inspections enabled.
5. Keep **Markdown > Incorrect table formatting** enabled.

IntelliJ IDEA 2026.2 stores custom-dictionary paths in local workspace state;
do not commit `workspace.xml` to share this pointer. The list itself is in Git.
Connect the dictionary once per checkout. Other editors need their native
custom-dictionary setting or a CSpell-compatible integration; no universal
automatic discovery contract is assumed.

## Format Tables

The repository uses IntelliJ IDEA's Markdown table formatter. This normalization
was performed with IntelliJ IDEA 2026.2.3, build IU-262.10968.63. In the editor,
select a table and use its **Reformat Table** action. Keep cell contents, links,
escaped pipes and alignment markers unchanged.

The installed command-line formatter can format a disposable copy:

```sh
"/Applications/IntelliJ IDEA.app/Contents/bin/format.sh" \
  -allowDefaults -r -m '*.md' /absolute/path/to/documentation-copy
```

It can also change non-table Markdown. Review its diff before transferring
changes; this table-normalization batch copied only the formatter's table
ranges and checked equality of parsed document tokens. `-dry` checks formatter
stability without writing. Formatting is not proof that prose, tables or code
examples are correct. CI's documentation graph, diagrams, spelling and source
contracts retain their existing responsibilities.

## Analyzer Exceptions

- Accepted technical terms belong only in the shared dictionary. This does not
  exempt their surrounding text from grammar, meaning or security review.
- Do not disable table formatting, all Markdown inspections, Grazie or
  Annotator to reduce a report count. Empty informational Mermaid annotations
  do not require a suppression.
- Do not add `http://authority` to **Ignored URLs**. The installed inspection
  uses prefix matching, which would also hide `http://authority.example`.
  The normalization formula in the
  [outbound proxy plan](../features/explicit-outbound-proxy-implementation-plan.md)
  is symbolic; that bounded interpretation does not authorize real HTTP calls
  or another occurrence elsewhere. Preserve HTTPS transport checks.
- A disputed grammar or security finding needs its exact source context,
  analyzer version, rationale and invalidation condition. A known false
  positive is not a permanent exemption for the whole file or rule.
- Do not add HTML suppression comments: the
  [documentation contract](../architecture/cross-cutting/documentation-authority.md)
  rejects raw HTML. Do not apply global phrase exceptions as a substitute for
  an unavailable project-scoped mechanism.

Recheck these settings and their actual effect after changing an IDE, plugin,
inspection profile or dictionary consumer. Exported report identity and a
fresh run are required before claiming warnings disappeared; syntax-valid
configuration alone is insufficient.

References: [JetBrains dictionaries](https://www.jetbrains.com/help/idea/spellchecking.html),
[command-line formatting](https://www.jetbrains.com/help/idea/command-line-formatter.html),
[CSpell dictionaries](https://cspell.org/docs/dictionaries/custom-dictionaries),
and [HTTP inspection](https://www.jetbrains.com/help/inspectopedia/HttpUrlsUsage.html).
