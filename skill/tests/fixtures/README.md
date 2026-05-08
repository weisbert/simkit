# SKILL test fixtures for `pvtProject.il`

Each file is a real `.pvtproject` candidate. The filename describes the case;
the contents are the smallest input that triggers it. Mapped one-to-one (or as
close as the SKILL test surface allows) to cases in
`tests/test_project_loader.py`.

| Fixture                              | Expected outcome (SKILL)              |
|--------------------------------------|---------------------------------------|
| `min_valid.pvtproject`               | `:ok`                                 |
| `full_example.pvtproject`            | `:ok` (every accept path)             |
| `bad_json_unterminated.pvtproject`   | `:err pvt_validation` (bubbled JSON)  |
| `bad_json_trailing_comma.pvtproject` | `:err pvt_validation` (bubbled JSON)  |
| `missing_project.pvtproject`         | `:err pvt_validation`                 |
| `missing_dbroot.pvtproject`          | `:err pvt_validation`                 |
| `project_uppercase.pvtproject`       | `:err pvt_validation` (regex)         |
| `project_with_spaces.pvtproject`     | `:err pvt_validation` (regex)         |
| `dbroot_empty.pvtproject`            | `:err pvt_validation`                 |
| `dbroot_wrong_type.pvtproject`       | `:err pvt_validation`                 |
| `author_null.pvtproject`             | `:ok` (author field becomes nil)      |
| `author_int.pvtproject`              | `:err pvt_validation`                 |
| `aliases_duplicate.pvtproject`       | `:err pvt_validation` (duplicate val) |
| `aliases_int_value.pvtproject`       | `:err pvt_validation` (non-string val)|
| `schema_version_999.pvtproject`      | `:err pvt_validation` (unsupported)   |
| `schema_version_bool.pvtproject`     | `:err pvt_validation` (bool sentinel) |
| `schema_version_string.pvtproject`   | `:err pvt_validation` (wrong type)    |
| `unknown_key.pvtproject`             | `:ok` (warns; field ignored)          |
| `underscore_keys.pvtproject`         | `:ok` (silent on `_`-prefixed keys)   |

## Notes

* `bad_json_*` fixtures yield a raw `pvt_json` parse error from `pvtJson.il`,
  which `pvtParsePvtProject` collapses to `pvt_validation` via
  `pvtErrToValidation` — matching the Python loader's behaviour of wrapping
  `json.JSONDecodeError` in `PvtProjectValidationError`.
* `full_example.pvtproject` carries an `_doc` key. The loader must NOT warn
  about it (per `docs/schema.md` §1 and the underscore-prefix convention).
* These fixtures are also reusable by a future Python-side equivalence harness.
