# ZyenLang Package Registry

`index.json` is the schema 1 package index used by `zy install NAME`.

```json
{
  "schema": 1,
  "packages": {
    "example": {
      "latest": "0.1.0",
      "versions": {
        "0.1.0": {
          "git": "https://github.com/owner/example.git",
          "rev": "FULL_40_OR_64_HEX_COMMIT"
        }
      }
    }
  }
}
```

Every version is immutable. Updating a release means adding a new version and
changing `latest`, never replacing the commit of an existing version. The
package's `zyproject.toml` name and version must match the index entry.

The registry does not contain build hooks, shell commands, compiler flags,
credentials, or binary archives. Native source included by a package is still
trusted code and must be reviewed before an entry is accepted.

Set `ZYEN_REGISTRY` to another HTTPS URL or an absolute local JSON path to use
a private or offline registry.
