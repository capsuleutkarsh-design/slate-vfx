# Third-party notices and credits

**Slate · © 2026 Utkarsh Tripathi · UT Community Licence 2.0**

Slate is made by **Utkarsh Tripathi** and released under the UT Community
Licence 2.0 (see [LICENSE.md](LICENSE.md)). This file covers the Icons8 icons
and product logos in `slate/resources/logos/`, and the OpenRV folder the Studio
installer ships (and what is left out of it on purpose). Other bundled parts
(FFmpeg, Qt/PySide6, Python, PostgreSQL and the Python packages) keep their own
licences and are not listed here yet.

---

## Icons

**Icons by Icons8 - https://icons8.com**

The icons in `slate/resources/logos/` whose names start with `icons8-` come from
[Icons8](https://icons8.com) and are used under the Icons8 free licence
(<https://icons8.com/license>), which asks desktop apps to show a link to
icons8.com in their About section. There are 36 of
them:

```
icons8-add-file-50.png                icons8-movies-folder-50.png
icons8-add-folder-50.png              icons8-music-folder-50.png
icons8-admin-50.png                   icons8-nuke-48.png
icons8-adobe-after-effects-48.png     icons8-paint-roller-50.png
icons8-adobe-premiere-pro-48.png      icons8-paste-50.png
icons8-archive-50.png                 icons8-pdf-50.png
icons8-blender-3d-48.png              icons8-pen-50.png
icons8-blender-3d-94.png              icons8-pencil-50.png
icons8-copy-to-folder-50.png          icons8-pictures-folder-50.png
icons8-create-50.png                  icons8-remove-50.png
icons8-cut-50.png                     icons8-ruler-50.png
icons8-database-administrator-50.png  icons8-scroll-50.png
icons8-delete-folder-50.png           icons8-shared-folder-50.png
icons8-design-50.png                  icons8-trash-50.png
icons8-edit-pencil-50.png             icons8-view-50.png
icons8-folder-50.png                  icons8-visual-effects-50.png
icons8-map-as-drive-50.png            icons8-writer-50.png
icons8-move-to-folder-50.png          icons8-writer-male-50.png
```

Four more files in the same folder are Icons8 icons under shorter names, which
is what the application launch buttons on a shot load. They are byte-for-byte
copies, not edits:

| File | Copy of |
|---|---|
| `after_effects.png` | `icons8-adobe-after-effects-48.png` |
| `blender.png` | `icons8-blender-3d-48.png` |
| `nuke.png` | `icons8-nuke-48.png` |
| `premiere.png` | `icons8-adobe-premiere-pro-48.png` |

### Product logos

The After Effects, Premiere Pro, Blender and Nuke icons, and the Natron,
Silhouette and OpenRV logos beside them (`natron.png`, `silhouette.png`,
`rv.png` and its copy `openrv-horizontal-black.png`, which are not Icons8
icons), show other companies' and projects' products. Those names and marks
belong to their owners - Adobe, the Blender Foundation, Foundry, Boris FX, the
Natron project, and the Academy Software Foundation (OpenRV). Slate uses them only to label the buttons that
open a shot in those applications. It is not made, endorsed or sponsored by any
of them.

---

## OpenRV

The Slate Studio installer (`deployment/setup_slate_client.iss`) ships OpenRV in
an `OpenRV` folder beside the program. OpenRV is released under the Apache
License 2.0 (<https://github.com/AcademySoftwareFoundation/OpenRV>) and keeps
that licence; the libraries inside it keep theirs.

**Qt5InsightTracker.dll is deliberately removed** from what the installer ships,
together with the rest of Qt Insight Tracker that came with the OpenRV build:
`Qt5InsightTrackerQml.dll`, the `qml\QtInsightTracker` folder and the
`insighttrackerplugin.dll` generic plugin. Qt Insight Tracker is available only
under a Qt commercial licence, so it cannot be passed on. OpenRV does not use
it: no program, library, plugin or QML file under `OpenRV` imports or names it,
other than the module's own parts. The removal is the `Excludes` list on the
OpenRV line of the installer script, so the copy in the `OpenRV` folder itself is
left as it was.

When OpenRV is updated, search the new build for `InsightTracker` again before
relying on this.
