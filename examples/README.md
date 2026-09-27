# ZyenLang Examples

The root manifest exposes each current example as a named target:

```powershell
zy run tour --project examples
zy run language-guide --project examples
zy run file-tree --project examples -- . output.txt
zy run gui-basic --project examples
zy run gui-button --project examples
zy run path --project examples
zy run struct-list --project examples
zy run task --project examples
zy run thread --project examples
zy run system-info --project examples
zy run http-server --project examples
```

| Target | Source | Purpose |
|---|---|---|
| `tour` | `language_tour.zy` | Core 0.3 syntax |
| `language-guide` | `language_reference.zy` | Broader language reference |
| `file-tree` | `file_tree.zy` | Filesystem and process arguments |
| `gui-basic` | `gui_basic.zy` | Application frame loop |
| `gui-button` | `gui_widgets.zy` | Retained widgets and callback |
| `path` | `path_basic.zy` | Cross-platform path helpers |
| `struct-list` | `struct_list.zy` | Managed List fields in structs |
| `task` | `task_basic.zy` | `spawn` and `await` |
| `thread` | `thread_basic.zy` | Thread standard-library helpers |
| `system-info` | `system_info.zy` | OS metadata and wall/monotonic time |
| `http-server` | `http_server.zy` | Function-value HTTP request routing |

Native ABI examples live below [`native/`](native/). Generated `target/`
directories are ignored and are not part of the source layout.
