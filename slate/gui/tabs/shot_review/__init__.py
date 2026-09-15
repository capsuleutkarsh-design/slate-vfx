"""
What is left of the Shot Review package: the Olive lineup editor.

The rest of it - a thousand-line tab, a comparison viewer, an image viewer, a
tech-check dialog and their workers - was reachable from nothing. The sidebar
stopped registering the tab when reviewing moved to OpenRV, and the code stayed
behind: it imported fine, it was maintained by accident, and its Approve button
would not have worked if anybody had found it, because it started an async sync
and never awaited it.

Only the lineup editor is still used, by the Timeline Viewer tab.
"""
